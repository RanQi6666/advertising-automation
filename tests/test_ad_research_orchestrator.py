import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.schemas.ad_research import AdResearchCreateRequest, CollectorAd
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
    async def is_technically_qualified(self, item):
        return True


class Model:
    async def plan_queries(self, **kwargs):
        return ["query"]

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


async def run(
    sizes: list[int], model: Model | None = None, *, target_count: int = 25
):
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
                target_count=target_count
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
