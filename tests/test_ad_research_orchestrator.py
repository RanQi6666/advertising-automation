import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.schemas.ad_research import AdResearchCreateRequest, CollectorAd
from backend.app.services.ad_research_media import TechnicalQualification
from backend.app.services.ad_research_orchestrator import AdResearchOrchestrator
from backend.app.services.ad_research_service import AdResearchService


def candidate(index: int) -> CollectorAd:
    return CollectorAd(
        ad_library_id=f"ad-{index}",
        advertiser_name="Example",
        status="ACTIVE",
        days_running=3,
        text_variants=["matching creative"],
        video_url=f"https://cdn.example/{index}.mp4",
        thumbnail_url=f"https://cdn.example/{index}.jpg",
        duration_seconds=20,
    )


class Collector:
    def __init__(self, sizes: list[int]) -> None:
        self.sizes = sizes
        self.calls = 0

    async def collect(self, **kwargs):
        self.calls += 1
        start = sum(self.sizes[: self.calls - 1])
        return [candidate(index) for index in range(start, start + self.sizes[self.calls - 1])]


class Media:
    async def inspect(self, item):
        return TechnicalQualification(
            qualified=True,
            reasons=(),
            duration_seconds=item.duration_seconds,
            active_days=item.days_running,
        )


class Model:
    async def plan_queries(self, **kwargs):
        return [f"query-{kwargs['round_number']}"]

    async def classify(self, *, category, candidate):
        return {
            "category_match": True,
            "category_confidence": 0.9,
            "business_type": category,
            "creative_relevance_score": 80,
            "public_performance_signal_score": 70,
            "real_money_signal_score": 0.0,
            "is_obviously_unrelated": False,
            "text_evidence": [],
            "visual_evidence": [],
            "public_signal_evidence": [],
            "public_risk_signals": [],
            "recommendation": "keep",
        }


class SlowModel(Model):
    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def classify(self, *, category, candidate):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            return await super().classify(category=category, candidate=candidate)
        finally:
            self.active -= 1


async def run(sizes: list[int], model: Model | None = None, *, target_count: int = 25):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        service = AdResearchService()
        created = await service.create_job(
            session,
            AdResearchCreateRequest(
                external_user_id=f"job-{sizes}-{target_count}",
                country="IN",
                category="gambling",
                target_count=target_count,
            ),
        )
        collector = Collector(sizes)
        result = await AdResearchOrchestrator(
            collector=collector, media=Media(), model=model or Model(), service=service
        ).run(session, created.job)
    await engine.dispose()
    return collector, result


@pytest.mark.asyncio
async def test_orchestrator_collects_again_when_first_round_is_short() -> None:
    collector, result = await run([10, 15])
    assert collector.calls == 2
    assert result.status == "completed"
    assert len(result.ads) == 25


@pytest.mark.asyncio
async def test_orchestrator_returns_insufficient_without_padding() -> None:
    _, result = await run([5, 0, 0, 0])
    assert result.status == "insufficient"
    assert len(result.ads) == 5
    assert result.summary["reason"] == "insufficient_qualified_ads"


@pytest.mark.asyncio
async def test_orchestrator_bounds_model_classification_concurrency(monkeypatch) -> None:
    monkeypatch.setenv("AD_RESEARCH_MODEL_CONCURRENCY", "2")
    get_settings.cache_clear()
    model = SlowModel()
    try:
        _, result = await run([25], model=model)
        assert result.status == "completed"
        assert model.max_active == 2
    finally:
        get_settings.cache_clear()


class ReclassifyingModel(Model):
    """Keeps a candidate only on its first classification call."""

    def __init__(self) -> None:
        self.classification_count_by_ad_id: dict[str, int] = {}

    async def classify(self, *, category, candidate):
        call_count = self.classification_count_by_ad_id.get(candidate.ad_library_id, 0) + 1
        self.classification_count_by_ad_id[candidate.ad_library_id] = call_count
        result = await super().classify(category=category, candidate=candidate)
        if call_count > 1:
            result.update(
                {
                    "category_match": False,
                    "category_confidence": 0.0,
                    "is_obviously_unrelated": True,
                    "recommendation": "exclude",
                }
            )
        return result


class ExcludingModel(Model):
    async def classify(self, *, category, candidate):
        result = await super().classify(category=category, candidate=candidate)
        result.update(
            {
                "category_match": False,
                "category_confidence": 0.2,
                "is_obviously_unrelated": True,
                "recommendation": "exclude",
            }
        )
        return result


@pytest.mark.asyncio
async def test_orchestrator_accumulates_keeps_from_prior_rounds() -> None:
    model = ReclassifyingModel()

    collector, result = await run([1, 1, 0, 0], model=model, target_count=2)

    assert result.status == "completed"
    assert [ad["ad_library_id"] for ad in result.ads] == ["ad-0", "ad-1"]
    assert collector.calls == 2
    assert model.classification_count_by_ad_id == {"ad-0": 1, "ad-1": 1}


@pytest.mark.asyncio
async def test_orchestrator_records_model_exclusion_diagnostics() -> None:
    _, result = await run([1, 0, 0, 0], model=ExcludingModel(), target_count=1)

    assert result.status == "insufficient"
    diagnostics = result.summary["classification_diagnostics"]
    assert diagnostics["classified_count"] == 1
    assert diagnostics["kept_count"] == 0
    assert diagnostics["excluded_count"] == 1
    assert diagnostics["candidates"] == [
        {
            "ad_library_id": "ad-0",
            "category_match": False,
            "category_confidence": 0.2,
            "business_type": "gambling",
            "creative_relevance_score": 80.0,
            "public_performance_signal_score": 70.0,
            "real_money_signal_score": 0.0,
            "is_obviously_unrelated": True,
            "recommendation": "exclude",
            "decision": "exclude",
            "exclusion_reasons": [
                "recommendation_not_keep",
                "category_not_matched",
                "obviously_unrelated",
                "category_confidence_below_threshold",
            ],
            "text_evidence": [],
            "visual_evidence": [],
            "public_signal_evidence": [],
            "public_risk_signals": [],
        }
    ]


class EmptyPlanningModel(Model):
    async def plan_queries(self, **kwargs):
        return []


@pytest.mark.asyncio
async def test_orchestrator_returns_insufficient_when_query_planning_is_empty() -> None:
    collector, result = await run([1], model=EmptyPlanningModel(), target_count=1)

    assert collector.calls == 0
    assert result.status == "insufficient"
    assert result.ads == []
    assert result.summary["classification_diagnostics"]["classified_count"] == 0


class QueryCollector:
    def __init__(self, results_by_query: dict[str, list[CollectorAd]]) -> None:
        self.results_by_query = results_by_query
        self.queries: list[str] = []

    async def collect(self, **kwargs):
        query = kwargs["query"]
        self.queries.append(query)
        return self.results_by_query.get(query.casefold().strip(), [])


class DiagnosticMedia:
    def __init__(self, rejected: dict[str, tuple[str, ...]] | None = None) -> None:
        self.rejected = rejected or {}

    async def inspect(self, item):
        reasons = self.rejected.get(item.ad_library_id, ())
        return TechnicalQualification(
            qualified=not reasons,
            reasons=reasons,
            duration_seconds=item.duration_seconds,
            active_days=item.days_running,
        )


class AdaptivePlanningModel(Model):
    def __init__(self, plans: list[list[str]], *, exclude_ids: set[str] | None = None) -> None:
        self.plans = plans
        self.exclude_ids = exclude_ids or set()
        self.plan_calls: list[dict] = []

    async def plan_queries(self, **kwargs):
        self.plan_calls.append(kwargs)
        index = len(self.plan_calls) - 1
        return self.plans[index] if index < len(self.plans) else []

    async def classify(self, *, category, candidate):
        result = await super().classify(category=category, candidate=candidate)
        if candidate.ad_library_id in self.exclude_ids:
            result.update(
                {
                    "category_match": False,
                    "category_confidence": 0.2,
                    "is_obviously_unrelated": True,
                    "recommendation": "exclude",
                }
            )
        return result


async def run_custom(*, collector, media, model, target_count: int):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as session:
        service = AdResearchService()
        created = await service.create_job(
            session,
            AdResearchCreateRequest(
                external_user_id=f"custom-{target_count}-{id(collector)}",
                country="IN",
                category="gambling",
                target_count=target_count,
            ),
        )
        result = await AdResearchOrchestrator(
            collector=collector, media=media, model=model, service=service
        ).run(session, created.job)
    await engine.dispose()
    return result


@pytest.mark.asyncio
async def test_orchestrator_feeds_rejection_evidence_into_next_query_round() -> None:
    technical_reject = candidate(100)
    model_reject = candidate(101)
    collector = QueryCollector({"first": [technical_reject, model_reject]})
    model = AdaptivePlanningModel([["first"], []], exclude_ids={model_reject.ad_library_id})

    result = await run_custom(
        collector=collector,
        media=DiagnosticMedia({technical_reject.ad_library_id: ("duration_over_30",)}),
        model=model,
        target_count=2,
    )

    assert result.status == "insufficient"
    second_gap = model.plan_calls[1]["gap_summary"]
    assert second_gap["previous_queries"] == ["first"]
    assert second_gap["technical_rejection_summary"] == {"duration_over_30": 1}
    assert second_gap["model_exclusion_summary"] == {
        "recommendation_not_keep": 1,
        "category_not_matched": 1,
        "obviously_unrelated": 1,
        "category_confidence_below_threshold": 1,
    }
    assert second_gap["duplicate_count"] == 0


@pytest.mark.asyncio
async def test_orchestrator_does_not_collect_a_query_twice_across_rounds() -> None:
    collector = QueryCollector(
        {
            "alpha": [candidate(200)],
            "beta": [candidate(201)],
        }
    )
    model = AdaptivePlanningModel([["Alpha"], [" alpha ", "Beta"], []])

    result = await run_custom(
        collector=collector,
        media=DiagnosticMedia(),
        model=model,
        target_count=3,
    )

    assert result.status == "insufficient"
    assert collector.queries == ["Alpha", "Beta"]
    assert [ad["ad_library_id"] for ad in result.ads] == ["ad-200", "ad-201"]


@pytest.mark.asyncio
async def test_orchestrator_summary_exposes_thresholds_rejections_and_rounds() -> None:
    rejected = candidate(300)
    kept = candidate(301)
    collector = QueryCollector({"first": [rejected, kept]})
    model = AdaptivePlanningModel([["first"], []])

    result = await run_custom(
        collector=collector,
        media=DiagnosticMedia({rejected.ad_library_id: ("active_days_below_minimum",)}),
        model=model,
        target_count=25,
    )

    assert result.status == "insufficient"
    assert len(result.ads) == 1
    assert result.summary["minimum_active_days"] == 1
    assert result.summary["maximum_video_seconds"] == 30.0
    assert result.summary["technical_rejection_summary"] == {"active_days_below_minimum": 1}
    assert result.summary["model_exclusion_summary"] == {}
    assert result.summary["rounds"][0]["queries"] == ["first"]
    assert result.summary["rounds"][0]["selected_count"] == 1
