from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.services.ad_research_orchestrator as orchestrator_module
from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.schemas.ad_research import AdResearchCreateRequest, CollectorAd
from backend.app.services.ad_research_media import PreparedAdMedia, TechnicalQualification
from backend.app.services.ad_research_model import AdResearchModel, PlannedQuery, QueryPlan
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
        if self.calls > len(self.sizes):
            return []
        start = sum(self.sizes[: self.calls - 1])
        return [candidate(index) for index in range(start, start + self.sizes[self.calls - 1])]


class Media:
    def __init__(
        self,
        rejected: dict[str, tuple[str, ...]] | None = None,
        frame_count_by_ad_id: dict[str, int] | None = None,
    ) -> None:
        self.rejected = rejected or {}
        self.frame_count_by_ad_id = frame_count_by_ad_id or {}
        self.retain_calls: list[set[str]] = []
        self.cleanup_calls: list[str] = []
        self.low_confidence_calls: list[str] = []
        self.inspect_calls_by_ad_id: dict[str, int] = {}

    async def inspect_many(
        self, items: list[CollectorAd], *, job_id: str
    ) -> dict[str, TechnicalQualification]:
        for item in items:
            self.inspect_calls_by_ad_id[item.ad_library_id] = (
                self.inspect_calls_by_ad_id.get(item.ad_library_id, 0) + 1
            )
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
        self.cleanup_calls.append(job_id)

    def _qualification(self, item: CollectorAd) -> TechnicalQualification:
        reasons = self.rejected.get(item.ad_library_id, ())
        return TechnicalQualification(
            qualified=not reasons,
            reasons=reasons,
            duration_seconds=item.duration_seconds,
            active_days=item.days_running,
            media=None
            if reasons
            else prepared_media(
                frame_count=self.frame_count_by_ad_id.get(item.ad_library_id, 3),
                marker=item.ad_library_id,
            ),
        )


class Model:
    def __init__(
        self,
        *,
        score_by_ad_id: dict[str, float] | None = None,
        confidence_by_ad_id: dict[str, float] | None = None,
        visual_priority_by_ad_id: dict[str, str] | None = None,
        fail_ids: set[str] | None = None,
        fail_once_ids: set[str] | None = None,
        plans: list[list[str]] | None = None,
    ) -> None:
        self.score_by_ad_id = score_by_ad_id or {}
        self.confidence_by_ad_id = confidence_by_ad_id or {}
        self.visual_priority_by_ad_id = visual_priority_by_ad_id or {}
        self.fail_ids = fail_ids or set()
        self.fail_once_ids = fail_once_ids or set()
        self.failed_once_ids: set[str] = set()
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
        self.score_calls_by_ad_id[marker] = self.score_calls_by_ad_id.get(marker, 0) + 1
        if marker in self.fail_ids:
            raise RuntimeError("model unavailable")
        if marker in self.fail_once_ids and marker not in self.failed_once_ids:
            self.failed_once_ids.add(marker)
            raise RuntimeError("model temporarily unavailable")
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
            "visual_priority": self.visual_priority_by_ad_id.get(marker, "game_gambling"),
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


async def run_custom(
    *,
    collector: Any,
    media: Media,
    model: Any,
    target_count: int,
    keywords: list[str] | None = None,
):
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
                    keywords=keywords or [],
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
async def test_orchestrator_completes_early_when_p1_reaches_target() -> None:
    first = candidate(200)
    second = candidate(201)
    model = Model(plans=[["first"], ["second"]])

    result = await run_custom(
        collector=QueryCollector({"first": [first, second]}),
        media=Media(),
        model=model,
        target_count=2,
    )

    assert result.status == "completed"
    assert len(result.ads) == 2
    assert len(model.plan_calls) == 1
    assert result.summary["priority_counts"]["game_gambling"] == 2


@pytest.mark.asyncio
async def test_orchestrator_executes_all_user_exact_slices_before_early_completion(
    monkeypatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    keywords = [f"keyword-{index:02d}" for index in range(1, 25)]
    first = candidate(202)

    class ContractPlanningModel(Model):
        def __init__(self) -> None:
            super().__init__(score_by_ad_id={first.ad_library_id: 90})
            self.planner = AdResearchModel()

        async def plan_queries(self, **kwargs: Any) -> QueryPlan:
            self.plan_calls.append(kwargs)
            return await self.planner.plan_queries(**kwargs)

    model = ContractPlanningModel()
    collector = QueryCollector({keywords[0]: [first]})
    try:
        result = await run_custom(
            collector=collector,
            media=Media(),
            model=model,
            target_count=1,
            keywords=keywords,
        )
    finally:
        get_settings.cache_clear()

    executed_exact = set(collector.queries).intersection(keywords)
    assert result.status == "completed"
    assert len(model.plan_calls) == 3
    assert executed_exact == set(keywords)
    assert result.summary["rounds"][-1]["round"] == 3


@pytest.mark.asyncio
async def test_orchestrator_uses_rounds_five_and_six_before_p4_fill() -> None:
    candidates = [candidate(210 + index) for index in range(6)]
    queries = [f"query-{index}" for index in range(1, 7)]
    model = Model(
        plans=[[query] for query in queries],
        visual_priority_by_ad_id={item.ad_library_id: "unrelated" for item in candidates},
    )

    result = await run_custom(
        collector=QueryCollector(dict(zip(queries, ([item] for item in candidates), strict=True))),
        media=Media(),
        model=model,
        target_count=6,
    )

    assert result.status == "completed"
    assert len(model.plan_calls) == 6
    assert result.summary["rounds"][-1]["round"] == 6
    assert result.summary["quality_summary"]["fallback_used"] is True
    for call in model.plan_calls[4:]:
        gap = call["gap_summary"]
        assert gap["quality_supplement_mode"] is True
        assert gap["query_metrics"]
        assert gap["priority_gaps"]
        assert gap["missing_play_patterns"]


@pytest.mark.asyncio
async def test_orchestrator_retries_one_model_failure() -> None:
    item = candidate(220)
    model = Model(plans=[["first"]], fail_once_ids={item.ad_library_id})

    result = await run_custom(
        collector=QueryCollector({"first": [item]}),
        media=Media(),
        model=model,
        target_count=1,
    )

    assert result.status == "completed"
    assert model.score_calls_by_ad_id[item.ad_library_id] == 2
    assert result.summary["model_scoring_failed"] == 0


@pytest.mark.asyncio
async def test_orchestrator_fails_without_partial_ads_when_scored_below_target() -> None:
    first = candidate(230)
    second = candidate(231)
    media = Media()
    model = Model(plans=[[f"query-{index}"] for index in range(1, 7)])

    result = await run_custom(
        collector=QueryCollector({"query-1": [first, second]}),
        media=media,
        model=model,
        target_count=3,
    )

    assert result.status == "failed"
    assert result.status != "insufficient"
    assert result.ads == []
    assert result.summary["selected_count"] == 0
    assert result.summary["reason"] == "insufficient_qualified_ads"
    assert len(media.cleanup_calls) == 1


@pytest.mark.asyncio
async def test_orchestrator_continues_after_zero_new_candidate_round() -> None:
    first = candidate(240)
    last = candidate(241)
    queries = [f"query-{index}" for index in range(1, 7)]
    model = Model(plans=[[query] for query in queries])

    result = await run_custom(
        collector=QueryCollector({"query-1": [first], "query-6": [last]}),
        media=Media(),
        model=model,
        target_count=2,
    )

    assert result.status == "completed"
    assert len(model.plan_calls) == 6
    round_new_candidates = [
        round_summary["round_new_candidates"] for round_summary in result.summary["rounds"]
    ]
    assert round_new_candidates == [1, 0, 0, 0, 0, 1]
    assert result.summary["rounds"][1]["diagnostics"] == ["no_new_candidates"]


@pytest.mark.asyncio
async def test_orchestrator_fails_when_raw_candidate_budget_is_exhausted(monkeypatch) -> None:
    monkeypatch.setattr(orchestrator_module, "MAX_RAW_CANDIDATES", 3)
    model = Model(plans=[["first"], ["second"]])

    result = await run_custom(
        collector=Collector([3]),
        media=Media(),
        model=model,
        target_count=4,
    )

    assert result.status == "failed"
    assert result.ads == []
    assert result.summary["raw_collected"] == 3
    assert result.summary["round_budget"]["max_raw_candidates"] == 3
    assert len(model.plan_calls) == 1


@pytest.mark.asyncio
async def test_orchestrator_collects_again_when_first_round_is_short() -> None:
    collector, media, result = await run([10, 15])

    assert collector.calls == 2
    assert result.status == "completed"
    assert len(result.ads) == 25
    assert result.summary["score_distribution"]["70_89"] == 25
    assert "twenty_fifth_score" not in result.summary
    assert media.retain_calls == [{ad["ad_library_id"] for ad in result.ads}]


@pytest.mark.asyncio
async def test_orchestrator_fails_without_padding_when_candidates_remain_below_target() -> None:
    _, media, result = await run([5, 0, 0, 0, 0, 0])

    assert result.status == "failed"
    assert result.ads == []
    assert result.summary["reason"] == "insufficient_qualified_ads"
    assert len(media.cleanup_calls) == 1
    assert "twenty_fifth_score" not in result.summary


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
async def test_orchestrator_stops_when_target_is_met_regardless_of_visual_score() -> None:
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
    assert collector.queries == ["first"]
    assert result.ads[0]["ad_library_id"] == low.ad_library_id
    assert result.ads[0]["final_score"] == result.ads[0]["visual_total"] == 40.0
    assert "twenty_fifth_score" not in result.summary["rounds"][0]


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
async def test_orchestrator_preserves_first_low_confidence_score_when_frame_enrichment_fails(
) -> None:
    item = candidate(400)
    collector = QueryCollector({"first": [item]})

    class FrameEnrichmentFailureMedia(Media):
        async def add_low_confidence_frames(
            self, item: CollectorAd, qualification: TechnicalQualification, *, job_id: str
        ) -> TechnicalQualification:
            self.low_confidence_calls.append(item.ad_library_id)
            raise RuntimeError("frame enrichment unavailable")

    media = FrameEnrichmentFailureMedia()
    model = Model(
        plans=[["first"]],
        confidence_by_ad_id={item.ad_library_id: 0.5},
        score_by_ad_id={item.ad_library_id: 75},
    )

    result = await run_custom(collector=collector, media=media, model=model, target_count=1)

    assert result.status == "completed"
    assert result.summary["model_scoring_failed"] == 0
    assert media.low_confidence_calls == [item.ad_library_id]
    assert model.score_calls_by_ad_id[item.ad_library_id] == 1
    assert result.ads[0]["visual_total"] == 75.0
    assert result.ads[0]["media"]["frame_count"] == 3


@pytest.mark.asyncio
async def test_orchestrator_preserves_first_low_confidence_score_when_follow_up_fails() -> None:
    item = candidate(401)
    collector = QueryCollector({"first": [item]})
    media = Media()

    class FollowUpFailureModel(Model):
        async def score_visual(self, **kwargs: Any) -> dict[str, Any]:
            score = await super().score_visual(**kwargs)
            marker = str(kwargs["media"].cover_url or "").split("/")[-2]
            if self.score_calls_by_ad_id[marker] == 2:
                raise RuntimeError("follow-up scoring unavailable")
            return score

    model = FollowUpFailureModel(
        plans=[["first"]],
        confidence_by_ad_id={item.ad_library_id: 0.5},
        score_by_ad_id={item.ad_library_id: 75},
    )

    result = await run_custom(collector=collector, media=media, model=model, target_count=1)

    assert result.status == "completed"
    assert result.summary["model_scoring_failed"] == 0
    assert media.low_confidence_calls == [item.ad_library_id]
    assert model.score_calls_by_ad_id[item.ad_library_id] == 2
    assert result.ads[0]["visual_total"] == 75.0
    assert result.ads[0]["media"]["frame_count"] == 3


@pytest.mark.asyncio
async def test_orchestrator_caps_oversized_collector_result_at_remaining_raw_budget(
    monkeypatch,
) -> None:
    monkeypatch.setattr(orchestrator_module, "MAX_RAW_CANDIDATES", 3)
    returned_ads = [candidate(500 + index) for index in range(10)]

    class OversizedCollector:
        def __init__(self) -> None:
            self.limits: list[int] = []

        async def collect(self, **kwargs: Any) -> list[CollectorAd]:
            self.limits.append(kwargs["limit"])
            return returned_ads

    collector = OversizedCollector()
    result = await run_custom(
        collector=collector,
        media=Media(),
        model=Model(plans=[["first"]]),
        target_count=4,
    )

    assert result.status == "failed"
    assert result.ads == []
    assert collector.limits == [3]
    assert result.summary["raw_collected"] == 3
    assert result.summary["deduplicated"] == 3


@pytest.mark.asyncio
async def test_orchestrator_records_model_scoring_failure_without_zero_score() -> None:
    failed = candidate(5)
    scored = candidate(6)
    collector = QueryCollector({"first": [failed, scored]})
    media = Media()
    model = Model(plans=[["first"]], fail_ids={failed.ad_library_id})

    result = await run_custom(collector=collector, media=media, model=model, target_count=2)

    assert result.status == "failed"
    assert result.ads == []
    assert model.score_calls_by_ad_id[failed.ad_library_id] == 2
    assert result.summary["model_scoring_failed"] == 1
    assert result.summary["model_scored"] == 1


@pytest.mark.asyncio
async def test_orchestrator_fails_when_query_planning_is_empty() -> None:
    collector = Collector([1])
    media = Media()
    model = Model(plans=[])

    result = await run_custom(collector=collector, media=media, model=model, target_count=1)

    assert collector.calls == 0
    assert result.status == "failed"
    assert result.ads == []
    assert result.summary["reason"] == "query_planning_empty"


@pytest.mark.asyncio
async def test_orchestrator_attributes_multi_source_ads_and_query_metrics() -> None:
    shared = candidate(7)
    second_only = candidate(8)
    duration_unavailable = candidate(9)
    duration_unavailable.duration_seconds = None
    plan = QueryPlan(
        queries=(
            PlannedQuery(
                query_id="r1_q01",
                query="first",
                intent="game_gambling",
                rationale="First structured query.",
            ),
            PlannedQuery(
                query_id="r1_q02",
                query="second",
                intent="sports_betting",
                rationale="Second structured query.",
            ),
        )
    )
    collector = QueryCollector(
        {"first": [shared, duration_unavailable], "second": [shared, second_only]}
    )
    media = Media(
        rejected={duration_unavailable.ad_library_id: ("duration_unavailable",)}
    )
    model = Model(plans=[plan])

    result = await run_custom(collector=collector, media=media, model=model, target_count=2)

    assert result.status == "completed"
    ads_by_id = {item["ad_library_id"]: item for item in result.ads}
    assert ads_by_id[shared.ad_library_id]["first_source_query_id"] == "r1_q01"
    assert ads_by_id[shared.ad_library_id]["source_query_ids"] == ["r1_q01", "r1_q02"]
    assert ads_by_id[second_only.ad_library_id]["first_source_query_id"] == "r1_q02"
    assert ads_by_id[second_only.ad_library_id]["source_query_ids"] == ["r1_q02"]
    assert model.score_calls_by_ad_id == {shared.ad_library_id: 1, second_only.ad_library_id: 1}

    required_metric_fields = {
        "raw_collected",
        "new_unique_count",
        "duplicate_count",
        "duration_le_30_count",
        "technical_qualified",
        "model_scored",
        "game_gambling_count",
        "sports_betting_count",
        "gambling_adjacent_count",
        "unrelated_count",
        "score_above_55",
        "average_visual_score",
        "best_visual_score",
    }
    query_metrics = result.summary["query_metrics"]
    assert [item["query_id"] for item in query_metrics] == ["r1_q01", "r1_q02"]
    assert all(required_metric_fields <= item.keys() for item in query_metrics)
    assert query_metrics == result.summary["rounds"][0]["query_metrics"]
    assert query_metrics[0] == {
        "query_id": "r1_q01",
        "query": "first",
        "intent": "game_gambling",
        "raw_collected": 2,
        "new_unique_count": 2,
        "duplicate_count": 0,
        "duration_le_30_count": 1,
        "technical_qualified": 1,
        "model_scored": 1,
        "game_gambling_count": 1,
        "sports_betting_count": 0,
        "gambling_adjacent_count": 0,
        "unrelated_count": 0,
        "score_above_55": 1,
        "average_visual_score": 80.0,
        "best_visual_score": 80.0,
    }
    assert query_metrics[1]["raw_collected"] == 2
    assert query_metrics[1]["new_unique_count"] == 1
    assert query_metrics[1]["duplicate_count"] == 1
    # Category and score metrics are attribution-inclusive: the shared ad counts for both sources.
    assert query_metrics[1]["technical_qualified"] == 2
    assert query_metrics[1]["model_scored"] == 2
    assert query_metrics[1]["game_gambling_count"] == 2


@pytest.mark.asyncio
async def test_orchestrator_passes_controlled_gap_summary_to_next_round() -> None:
    first = candidate(17)
    second = candidate(18)
    first.advertiser_name = "Sensitive advertiser"
    first.text_variants = ["Sensitive ad copy"]
    first.headline = "Sensitive headline"
    first.cta_text = "Sensitive CTA"
    first.ad_snapshot_url = "https://sensitive.example/landing"
    first.video_url = "https://sensitive.example/video.mp4"
    plan_one = QueryPlan(
        queries=(
            PlannedQuery(
                query_id="r1_q01",
                query="first",
                intent="game_gambling",
                rationale="Initial query.",
            ),
        )
    )
    plan_two = QueryPlan(
        queries=(
            PlannedQuery(
                query_id="r2_q01",
                query="second",
                intent="sports_betting",
                rationale="Supplement query.",
            ),
        )
    )
    collector = QueryCollector({"first": [first], "second": [second]})
    media = Media()
    model = Model(plans=[plan_one, plan_two])

    result = await run_custom(collector=collector, media=media, model=model, target_count=2)

    assert result.status == "completed"
    second_gap = model.plan_calls[1]["gap_summary"]
    assert {
        "target_count",
        "missing_count",
        "priority_counts",
        "query_metrics",
        "previous_queries",
        "technical_rejection_summary",
        "model_scoring_failed",
    } <= second_gap.keys()
    assert second_gap["target_count"] == 2
    assert second_gap["missing_count"] == 1
    assert second_gap["previous_queries"] == ["first"]
    assert set(second_gap["priority_counts"]) == {
        "game_gambling",
        "sports_betting",
        "gambling_adjacent",
        "unrelated",
    }
    assert second_gap["query_metrics"][0]["query_id"] == "r1_q01"
    assert "high_score_visible_elements" not in second_gap
    serialized_gap = repr(second_gap)
    for forbidden in (
        "Sensitive advertiser",
        "Sensitive ad copy",
        "Sensitive headline",
        "Sensitive CTA",
        "sensitive.example",
    ):
        assert forbidden not in serialized_gap


@pytest.mark.asyncio
async def test_orchestrator_does_not_collect_a_query_twice_across_rounds() -> None:
    collector = QueryCollector({"alpha": [candidate(9)], "beta": [candidate(10)]})
    media = Media()
    model = Model(plans=[["Alpha"], [" alpha ", "Beta"], []])

    result = await run_custom(collector=collector, media=media, model=model, target_count=3)

    assert result.status == "failed"
    assert collector.queries == ["Alpha", "Beta"]
    assert result.ads == []


@pytest.mark.asyncio
async def test_orchestrator_summary_exposes_visual_scores_and_technical_rejections() -> None:
    rejected = candidate(11)
    kept = candidate(12, active_days=30)
    collector = QueryCollector({"first": [rejected, kept]})
    media = Media({rejected.ad_library_id: ("active_days_below_minimum",)})
    model = Model(plans=[["first"]])

    result = await run_custom(collector=collector, media=media, model=model, target_count=25)

    assert result.status == "failed"
    assert result.ads == []
    assert result.summary["minimum_active_days"] == 1
    assert result.summary["maximum_video_seconds"] == 30.0
    assert result.summary["technical_rejection_summary"] == {"active_days_below_minimum": 1}
    assert result.summary["score_distribution"]["70_89"] == 1
    assert result.summary["rounds"][0]["queries"] == ["first"]
    assert result.summary["rounds"][0]["selected_count"] == 1


@pytest.mark.asyncio
async def test_orchestrator_ranks_game_gambling_before_higher_scoring_sports_betting() -> None:
    game = candidate(101)
    sports = candidate(102)
    collector = QueryCollector({"first": [sports, game]})
    model = Model(
        plans=[["first"], ["second"], ["third"], ["fourth"], ["fifth"], ["sixth"]],
        score_by_ad_id={game.ad_library_id: 30, sports.ad_library_id: 95},
        visual_priority_by_ad_id={
            game.ad_library_id: "game_gambling",
            sports.ad_library_id: "sports_betting",
        },
    )

    result = await run_custom(collector=collector, media=Media(), model=model, target_count=2)

    assert [ad["ad_library_id"] for ad in result.ads] == [game.ad_library_id, sports.ad_library_id]


@pytest.mark.asyncio
async def test_orchestrator_uses_all_secondary_visual_sort_tie_breakers_deterministically() -> None:
    visual_total = candidate(110, active_days=1)
    confidence = candidate(111, active_days=1)
    active_days = candidate(112, active_days=10)
    frame_count = candidate(113, active_days=5)
    ad_id_first = candidate(114, active_days=5)
    ad_id_last = candidate(115, active_days=5)
    candidates = [ad_id_last, frame_count, active_days, confidence, visual_total, ad_id_first]
    score_by_ad_id = {item.ad_library_id: 80 for item in candidates}
    score_by_ad_id[visual_total.ad_library_id] = 90
    confidence_by_ad_id = {item.ad_library_id: 0.8 for item in candidates}
    confidence_by_ad_id[visual_total.ad_library_id] = 0.1
    confidence_by_ad_id[confidence.ad_library_id] = 0.9
    media = Media(
        frame_count_by_ad_id={
            frame_count.ad_library_id: 5,
            ad_id_first.ad_library_id: 3,
            ad_id_last.ad_library_id: 3,
        }
    )

    result = await run_custom(
        collector=QueryCollector({"first": candidates}),
        media=media,
        model=Model(
            plans=[["first"]],
            score_by_ad_id=score_by_ad_id,
            confidence_by_ad_id=confidence_by_ad_id,
        ),
        target_count=6,
    )

    assert [ad["ad_library_id"] for ad in result.ads] == [
        visual_total.ad_library_id,
        confidence.ad_library_id,
        active_days.ad_library_id,
        frame_count.ad_library_id,
        ad_id_first.ad_library_id,
        ad_id_last.ad_library_id,
    ]


@pytest.mark.asyncio
async def test_orchestrator_marks_only_selected_unrelated_ads_as_transparent_fallbacks() -> None:
    game = candidate(120)
    sports = candidate(121)
    adjacent = candidate(122)
    fallback = candidate(123)
    unselected_fallback = candidate(124)
    candidates = [unselected_fallback, fallback, adjacent, sports, game]
    model = Model(
        plans=[["first"], ["second"], ["third"], ["fourth"], ["fifth"], ["sixth"]],
        score_by_ad_id={
            game.ad_library_id: 10,
            sports.ad_library_id: 90,
            adjacent.ad_library_id: 80,
            fallback.ad_library_id: 70,
            unselected_fallback.ad_library_id: 60,
        },
        visual_priority_by_ad_id={
            game.ad_library_id: "game_gambling",
            sports.ad_library_id: "sports_betting",
            adjacent.ad_library_id: "gambling_adjacent",
            fallback.ad_library_id: "unrelated",
            unselected_fallback.ad_library_id: "unrelated",
        },
    )

    result = await run_custom(
        collector=QueryCollector({"first": candidates}),
        media=Media(),
        model=model,
        target_count=4,
    )

    assert [ad["ad_library_id"] for ad in result.ads] == [
        game.ad_library_id,
        sports.ad_library_id,
        adjacent.ad_library_id,
        fallback.ad_library_id,
    ]
    assert [ad["is_fallback"] for ad in result.ads] == [False, False, False, True]
    assert [ad["fallback_reason"] for ad in result.ads] == [
        None,
        None,
        None,
        "insufficient_high_relevance_candidates",
    ]
    assert result.summary["quality_summary"] == {
        "game_gambling_count": 1,
        "sports_betting_count": 1,
        "gambling_adjacent_count": 1,
        "fallback_count": 1,
        "fallback_used": True,
    }


@pytest.mark.asyncio
async def test_orchestrator_final_score_is_exact_validated_visual_total_without_continuity() -> (
    None
):
    item = candidate(130, active_days=30)
    result = await run_custom(
        collector=QueryCollector({"first": [item]}),
        media=Media(),
        model=Model(plans=[["first"]], score_by_ad_id={item.ad_library_id: 80}),
        target_count=1,
    )

    ad = result.ads[0]
    recomputed_visual_total = sum(
        ad[key]
        for key in (
            "gameplay_gambling_points",
            "multi_signal_style_points",
            "betting_mechanism_points",
            "gambling_visual_style_points",
            "visual_clarity_points",
            "media_quality_points",
        )
    )
    assert ad["final_score"] == ad["visual_total"] == recomputed_visual_total == 80.0
    assert ad["active_days"] == 30
    assert {
        "visual_priority",
        "gambling_signals",
        "game_visual_present",
        "is_fallback",
        "fallback_reason",
    } <= ad.keys()
    assert ad["is_fallback"] is False
    assert ad["fallback_reason"] is None
    assert "public_continuity_points" not in ad


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


@pytest.mark.asyncio
async def test_orchestrator_refreshes_scored_ad_sources_across_rounds_without_rescoring() -> None:
    shared = candidate(19)
    second_only = candidate(20)
    first_plan = QueryPlan(
        queries=(
            PlannedQuery(
                query_id="r1_q01",
                query="first",
                intent="game_gambling",
                rationale="Initial query.",
            ),
        )
    )
    second_plan = QueryPlan(
        queries=(
            PlannedQuery(
                query_id="r2_q01",
                query="second",
                intent="sports_betting",
                rationale="Supplement query.",
            ),
        )
    )
    collector = QueryCollector({"first": [shared], "second": [shared, second_only]})
    media = Media()
    model = Model(plans=[first_plan, second_plan])

    result = await run_custom(collector=collector, media=media, model=model, target_count=2)

    assert result.status == "completed"
    ads_by_id = {item["ad_library_id"]: item for item in result.ads}
    assert ads_by_id[shared.ad_library_id]["first_source_query_id"] == "r1_q01"
    assert ads_by_id[shared.ad_library_id]["source_query_ids"] == ["r1_q01", "r2_q01"]
    assert model.score_calls_by_ad_id == {shared.ad_library_id: 1, second_only.ad_library_id: 1}
    assert media.inspect_calls_by_ad_id == {shared.ad_library_id: 1, second_only.ad_library_id: 1}


@pytest.mark.asyncio
async def test_orchestrator_skips_reused_or_wrong_round_structured_query_ids() -> None:
    first = candidate(21)
    second = candidate(22)
    first_plan = QueryPlan(
        queries=(
            PlannedQuery(
                query_id="r1_q01",
                query="first",
                intent="game_gambling",
                rationale="Initial query.",
            ),
        )
    )
    second_plan = QueryPlan(
        queries=(
            PlannedQuery(
                query_id="r1_q01",
                query="duplicate id",
                intent="game_gambling",
                rationale="Invalid repeat.",
            ),
            PlannedQuery(
                query_id="r1_q02",
                query="wrong round",
                intent="sports_betting",
                rationale="Invalid round.",
            ),
            PlannedQuery(
                query_id="r2_q01",
                query="second",
                intent="local_exploration",
                rationale="Valid second-round query.",
            ),
        )
    )
    collector = QueryCollector({"first": [first], "second": [second]})
    result = await run_custom(
        collector=collector,
        media=Media(),
        model=Model(plans=[first_plan, second_plan]),
        target_count=3,
    )

    assert result.status == "failed"
    assert collector.queries == ["first", "second"]
    assert [item["query_id"] for item in result.summary["query_metrics"]] == ["r1_q01", "r2_q01"]
    round_two = result.summary["rounds"][1]
    assert round_two["skipped_query_count"] == 2
    assert "skipped_repeated_query_count" not in round_two
    assert round_two["skipped_query_diagnostics"] == [
        {"query_id": "r1_q01", "reason": "query_id_already_used"},
        {"query_id": "r1_q02", "reason": "query_id_wrong_round"},
    ]


@pytest.mark.asyncio
async def test_orchestrator_accepts_valid_second_round_structured_query_plan() -> None:
    first = candidate(23)
    second = candidate(24)
    first_plan = QueryPlan(
        queries=(
            PlannedQuery(
                query_id="r1_q01",
                query="first",
                intent="game_gambling",
                rationale="Initial query.",
            ),
        )
    )
    second_plan = QueryPlan(
        queries=(
            PlannedQuery(
                query_id="r2_q01",
                query="second",
                intent="sports_betting",
                rationale="Valid second-round query.",
            ),
        )
    )
    collector = QueryCollector({"first": [first], "second": [second]})

    result = await run_custom(
        collector=collector,
        media=Media(),
        model=Model(plans=[first_plan, second_plan]),
        target_count=2,
    )

    assert result.status == "completed"
    assert collector.queries == ["first", "second"]
    assert [item["query_id"] for item in result.summary["query_metrics"]] == ["r1_q01", "r2_q01"]
    assert result.summary["rounds"][1]["skipped_query_diagnostics"] == []
