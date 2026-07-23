from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.schemas.ad_research import AdResearchCreateRequest, CollectorAd
from backend.app.services.ad_research_media import PreparedAdMedia, TechnicalQualification
from backend.app.services.ad_research_model import PlannedQuery, QueryPlan
from backend.app.services.ad_research_orchestrator import AdResearchOrchestrator
from backend.app.services.ad_research_service import AdResearchService


def candidate(index: int, *, active_days: int = 3) -> CollectorAd:
    return CollectorAd(
        ad_library_id=f"ad-{index}",
        advertiser_name="Example",
        status="ACTIVE",
        days_running=active_days,
        text_variants=["unrelated text is not a visual hard gate"],
        video_url=f"https://cdn.example/{index}.mp4",
        thumbnail_url=f"https://cdn.example/{index}.jpg",
        duration_seconds=20,
    )


def prepared_media(*, frame_count: int = 3, marker: str = "default") -> PreparedAdMedia:
    frame_urls = tuple(
        f"https://ai.example/storage/frame-{index}.jpg" for index in range(frame_count)
    )
    return PreparedAdMedia(
        cover_url=f"https://ai.example/storage/{marker}/cover.jpg",
        cover_source="generated_frame",
        frame_urls=frame_urls,
        local_frame_paths=tuple(Path(f"frame-{index}.jpg") for index in range(frame_count)),
        duration_source="collector",
        duration_probe_attempts=0,
    )


class Collector:
    def __init__(self, sizes: list[int]) -> None:
        self.sizes = sizes
        self.calls = 0

    async def collect(self, **kwargs: Any) -> list[CollectorAd]:
        self.calls += 1
        start = sum(self.sizes[: self.calls - 1])
        return [candidate(index) for index in range(start, start + self.sizes[self.calls - 1])]


class Media:
    def __init__(self, rejected: dict[str, tuple[str, ...]] | None = None) -> None:
        self.rejected = rejected or {}
        self.retain_calls: list[set[str]] = []
        self.low_confidence_calls: list[str] = []

    async def inspect_many(
        self, items: list[CollectorAd], *, job_id: str
    ) -> dict[str, TechnicalQualification]:
        return {item.ad_library_id: self._qualification(item) for item in items}

    async def add_low_confidence_frames(
        self, item: CollectorAd, qualification: TechnicalQualification, *, job_id: str
    ) -> TechnicalQualification:
        self.low_confidence_calls.append(item.ad_library_id)
        assert qualification.media is not None
        return replace(
            qualification,
            media=prepared_media(frame_count=5, marker=item.ad_library_id),
        )

    async def retain_only(self, job_id: str, ad_library_ids: set[str]) -> None:
        self.retain_calls.append(ad_library_ids)

    async def cleanup_job_media(self, job_id: str) -> None:
        return None

    def _qualification(self, item: CollectorAd) -> TechnicalQualification:
        reasons = self.rejected.get(item.ad_library_id, ())
        return TechnicalQualification(
            qualified=not reasons,
            reasons=reasons,
            duration_seconds=item.duration_seconds,
            active_days=item.days_running,
            media=None if reasons else prepared_media(marker=item.ad_library_id),
        )


class Model:
    def __init__(
        self,
        *,
        score_by_ad_id: dict[str, float] | None = None,
        confidence_by_ad_id: dict[str, float] | None = None,
        fail_ids: set[str] | None = None,
        plans: list[list[str]] | None = None,
    ) -> None:
        self.score_by_ad_id = score_by_ad_id or {}
        self.confidence_by_ad_id = confidence_by_ad_id or {}
        self.fail_ids = fail_ids or set()
        self.plans = plans
        self.plan_calls: list[dict[str, Any]] = []
        self.score_calls_by_ad_id: dict[str, int] = {}

    async def plan_queries(self, **kwargs: Any) -> list[str]:
        self.plan_calls.append(kwargs)
        if self.plans is not None:
            index = len(self.plan_calls) - 1
            return self.plans[index] if index < len(self.plans) else []
        return [f"query-{kwargs['round_number']}"]

    async def score_visual(
        self, *, category: str, duration_seconds: float, media: PreparedAdMedia
    ) -> dict[str, Any]:
        marker = str(media.cover_url or "").split("/")[-2]
        if marker in self.fail_ids:
            raise RuntimeError("model unavailable")
        self.score_calls_by_ad_id[marker] = self.score_calls_by_ad_id.get(marker, 0) + 1
        total = self.score_by_ad_id.get(marker, 80.0)
        confidence = self.confidence_by_ad_id.get(marker, 0.9)
        remaining = max(float(total), 0.0)
        dimensions: dict[str, float] = {}
        for key, maximum in (
            ("gameplay_gambling_points", 40.0),
            ("multi_signal_style_points", 20.0),
            ("betting_mechanism_points", 15.0),
            ("gambling_visual_style_points", 10.0),
            ("visual_clarity_points", 10.0),
            ("media_quality_points", 5.0),
        ):
            dimensions[key] = min(remaining, maximum)
            remaining -= dimensions[key]
        return {
            "visual_priority": "game_gambling",
            **dimensions,
            "visual_total": -9999,
            "analysis_confidence": confidence,
            "gambling_signals": [f"visible-{marker}", "slot ui"],
            "game_visual_present": True,
            "visual_evidence": [{"frame_index": 0, "detail": "slot ui"}],
            "retrieval_hints": ["slot ui"],
            "uncertain": False,
        }


class SlowModel(Model):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0

    async def score_visual(self, **kwargs: Any) -> dict[str, Any]:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.01)
            return await super().score_visual(**kwargs)
        finally:
            self.active -= 1


class QueryCollector:
    def __init__(self, results_by_query: dict[str, list[CollectorAd]]) -> None:
        self.results_by_query = results_by_query
        self.queries: list[str] = []

    async def collect(self, **kwargs: Any) -> list[CollectorAd]:
        query = kwargs["query"]
        self.queries.append(query)
        return self.results_by_query.get(query.casefold().strip(), [])


async def run_custom(*, collector: Any, media: Media, model: Model, target_count: int):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
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
            return await AdResearchOrchestrator(
                collector=collector, media=media, model=model, service=service
            ).run(session, created.job)
    finally:
        await engine.dispose()


async def run(sizes: list[int], model: Model | None = None, *, target_count: int = 25):
    collector = Collector(sizes)
    media = Media()
    result = await run_custom(
        collector=collector, media=media, model=model or Model(), target_count=target_count
    )
    return collector, media, result


@pytest.mark.asyncio
async def test_orchestrator_collects_again_when_first_round_is_short() -> None:
    collector, media, result = await run([10, 15])

    assert collector.calls == 2
    assert result.status == "completed"
    assert len(result.ads) == 25
    assert result.summary["twenty_fifth_score"] == 83.0
    assert media.retain_calls == [{ad["ad_library_id"] for ad in result.ads}]


@pytest.mark.asyncio
async def test_orchestrator_returns_insufficient_without_padding() -> None:
    _, _, result = await run([5, 0, 0, 0])

    assert result.status == "insufficient"
    assert len(result.ads) == 5
    assert result.summary["reason"] == "no_new_candidates"
    assert result.summary["twenty_fifth_score"] is None


@pytest.mark.asyncio
async def test_orchestrator_bounds_visual_model_concurrency(monkeypatch) -> None:
    monkeypatch.setenv("AD_RESEARCH_MODEL_CONCURRENCY", "2")
    get_settings.cache_clear()
    model = SlowModel()
    try:
        _, _, result = await run([25], model=model)
        assert result.status == "completed"
        assert model.max_active == 2
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_orchestrator_supplements_when_target_score_is_too_low() -> None:
    low = candidate(1)
    high = candidate(2)
    collector = QueryCollector({"first": [low], "second": [high]})
    media = Media()
    model = Model(
        plans=[["first"], ["second"]],
        score_by_ad_id={low.ad_library_id: 40, high.ad_library_id: 80},
    )

    result = await run_custom(collector=collector, media=media, model=model, target_count=1)

    assert result.status == "completed"
    assert collector.queries == ["first", "second"]
    assert result.ads[0]["ad_library_id"] == high.ad_library_id
    assert result.summary["rounds"][0]["twenty_fifth_score"] == 43.0


@pytest.mark.asyncio
async def test_orchestrator_does_not_apply_text_or_category_hard_rejection() -> None:
    item = candidate(3)
    item.text_variants = ["recipe blog and shopping"]
    collector = QueryCollector({"first": [item]})
    media = Media()
    model = Model(plans=[["first"]], score_by_ad_id={item.ad_library_id: 70})

    result = await run_custom(collector=collector, media=media, model=model, target_count=1)

    assert result.status == "completed"
    assert result.ads[0]["visual_total"] == 70.0
    assert "category_match" not in result.ads[0]
    assert result.summary["model_relevant"] == result.summary["model_scored"] == 1


@pytest.mark.asyncio
async def test_orchestrator_passes_only_category_duration_and_media_to_visual_model() -> None:
    item = candidate(31, active_days=17)
    item.text_variants = ["candidate text must not reach visual scorer"]
    collector = QueryCollector({"first": [item]})
    media = Media()

    class StrictVisualModel(Model):
        def __init__(self) -> None:
            super().__init__(plans=[["first"]])
            self.calls: list[dict[str, Any]] = []

        async def score_visual(
            self, *, category: str, duration_seconds: float, media: PreparedAdMedia
        ) -> dict[str, Any]:
            self.calls.append(
                {"category": category, "duration_seconds": duration_seconds, "media": media}
            )
            return await super().score_visual(
                category=category, duration_seconds=duration_seconds, media=media
            )

    model = StrictVisualModel()
    result = await run_custom(collector=collector, media=media, model=model, target_count=1)

    assert result.status == "completed"
    assert len(model.calls) == 1
    assert model.calls[0]["category"] == "gambling"
    assert model.calls[0]["duration_seconds"] == 20.0
    assert isinstance(model.calls[0]["media"], PreparedAdMedia)


@pytest.mark.asyncio
async def test_orchestrator_rescores_low_confidence_candidate_with_extra_frames() -> None:
    item = candidate(4)
    collector = QueryCollector({"first": [item]})
    media = Media()
    model = Model(
        plans=[["first"]],
        confidence_by_ad_id={item.ad_library_id: 0.5},
        score_by_ad_id={item.ad_library_id: 75},
    )

    result = await run_custom(collector=collector, media=media, model=model, target_count=1)

    assert result.status == "completed"
    assert media.low_confidence_calls == [item.ad_library_id]
    assert model.score_calls_by_ad_id[item.ad_library_id] == 2
    assert result.ads[0]["media"]["frame_count"] == 5


@pytest.mark.asyncio
async def test_orchestrator_records_model_scoring_failure_without_zero_score() -> None:
    failed = candidate(5)
    scored = candidate(6)
    collector = QueryCollector({"first": [failed, scored]})
    media = Media()
    model = Model(plans=[["first"]], fail_ids={failed.ad_library_id})

    result = await run_custom(collector=collector, media=media, model=model, target_count=2)

    assert result.status == "insufficient"
    assert [ad["ad_library_id"] for ad in result.ads] == [scored.ad_library_id]
    assert result.summary["model_scoring_failed"] == 1
    assert result.summary["model_scored"] == 1


@pytest.mark.asyncio
async def test_orchestrator_returns_insufficient_when_query_planning_is_empty() -> None:
    collector = Collector([1])
    media = Media()
    model = Model(plans=[])

    result = await run_custom(collector=collector, media=media, model=model, target_count=1)

    assert collector.calls == 0
    assert result.status == "insufficient"
    assert result.ads == []
    assert result.summary["reason"] == "query_planning_empty"


@pytest.mark.asyncio
async def test_orchestrator_feeds_high_score_visual_elements_into_next_round() -> None:
    first = candidate(7)
    second = candidate(8)
    collector = QueryCollector({"first": [first], "second": [second]})
    media = Media()
    model = Model(plans=[["first"], ["second"]])

    result = await run_custom(collector=collector, media=media, model=model, target_count=2)

    assert result.status == "completed"
    second_gap = model.plan_calls[1]["gap_summary"]
    assert second_gap["previous_queries"] == ["first"]
    assert second_gap["technical_rejection_summary"] == {}
    assert second_gap["high_score_visible_elements"] == ["visible-ad-7", "slot ui"]


@pytest.mark.asyncio
async def test_orchestrator_does_not_collect_a_query_twice_across_rounds() -> None:
    collector = QueryCollector({"alpha": [candidate(9)], "beta": [candidate(10)]})
    media = Media()
    model = Model(plans=[["Alpha"], [" alpha ", "Beta"], []])

    result = await run_custom(collector=collector, media=media, model=model, target_count=3)

    assert result.status == "insufficient"
    assert collector.queries == ["Alpha", "Beta"]
    assert [ad["ad_library_id"] for ad in result.ads] == ["ad-10", "ad-9"]


@pytest.mark.asyncio
async def test_orchestrator_summary_exposes_visual_scores_and_technical_rejections() -> None:
    rejected = candidate(11)
    kept = candidate(12, active_days=30)
    collector = QueryCollector({"first": [rejected, kept]})
    media = Media({rejected.ad_library_id: ("active_days_below_minimum",)})
    model = Model(plans=[["first"]])

    result = await run_custom(collector=collector, media=media, model=model, target_count=25)

    assert result.status == "insufficient"
    assert len(result.ads) == 1
    assert result.ads[0]["public_continuity_points"] == 10.0
    assert result.summary["minimum_active_days"] == 1
    assert result.summary["maximum_video_seconds"] == 30.0
    assert result.summary["technical_rejection_summary"] == {"active_days_below_minimum": 1}
    assert result.summary["score_distribution"]["90_100"] == 1
    assert result.summary["rounds"][0]["queries"] == ["first"]
    assert result.summary["rounds"][0]["selected_count"] == 1


@pytest.mark.asyncio
async def test_orchestrator_collects_plain_query_text_from_structured_query_plan() -> None:
    item = candidate(13)
    collector = QueryCollector({"rummy bonus": [item]})
    media = Media()
    model = Model(
        plans=[
            QueryPlan(
                queries=(
                    PlannedQuery(
                        query_id="r1_q01",
                        query="rummy bonus",
                        intent="local_exploration",
                        rationale="Structured planner integration.",
                        expected_visuals=("slot reels",),
                    ),
                )
            )
        ],
        score_by_ad_id={item.ad_library_id: 80},
    )

    result = await run_custom(collector=collector, media=media, model=model, target_count=1)

    assert result.status == "completed"
    assert collector.queries == ["rummy bonus"]
    assert all("PlannedQuery(" not in query for query in collector.queries)
