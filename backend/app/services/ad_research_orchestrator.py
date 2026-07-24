from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.core.errors import ProviderError
from backend.app.db.base import utcnow
from backend.app.db.models.ad_research_job import AdResearchJob
from backend.app.db.models.generation_task import GenerationTask
from backend.app.schemas.ad_research import CollectorAd
from backend.app.services.ad_research_collector import AdSourceAdapter, MetaAdsBridgeAdapter
from backend.app.services.ad_research_media import (
    MAX_VIDEO_SECONDS,
    MIN_ACTIVE_DAYS,
    AdResearchMediaInspector,
    PreparedAdMedia,
    TechnicalQualification,
)
from backend.app.services.ad_research_model import (
    AdResearchModel,
    QueryPlan,
    _validated_visual_score,
)
from backend.app.services.ad_research_ranking import (
    quality_summary as build_quality_summary,
)
from backend.app.services.ad_research_ranking import (
    select_ranked,
    visual_sort_key,
)
from backend.app.services.ad_research_service import AdResearchService

STANDARD_ROUNDS = 4
MAX_ROUNDS = 6
MAX_RAW_CANDIDATES = 650
PER_QUERY_LIMIT = 50
MODEL_SCORE_ATTEMPTS = 3
LOW_CONFIDENCE_THRESHOLD = 0.60

_QUALITY_SUPPLEMENT_SIGNALS = {
    "game_gambling": ("game ui", "slot reels", "slot ui"),
    "sports_betting": ("sports odds board",),
    "gambling_adjacent": ("wallet or balance ui", "reward animation"),
}


@dataclass(frozen=True)
class AdResearchRunResult:
    status: str
    ads: list[dict[str, Any]]
    summary: dict[str, Any]


@dataclass(frozen=True)
class _ResolvedQuery:
    query_id: str
    query: str
    intent: str
    query_origin: str = "model_exploration"
    parent_keyword: str | None = None


class AdResearchOrchestrator:
    def __init__(
        self,
        *,
        collector: AdSourceAdapter | None = None,
        media: AdResearchMediaInspector | None = None,
        model: AdResearchModel | None = None,
        service: AdResearchService | None = None,
    ) -> None:
        self.collector = collector or MetaAdsBridgeAdapter()
        self.media = media or AdResearchMediaInspector()
        self.model = model or AdResearchModel()
        self.service = service or AdResearchService()
        self.settings = get_settings()

    async def run(self, session: AsyncSession, job: AdResearchJob) -> AdResearchRunResult:
        job.status = "processing"
        job.stage = "planning"
        job.started_at = job.started_at or utcnow()
        await session.commit()

        seen: dict[str, CollectorAd] = {}
        source_query_ids_by_ad_library_id: dict[str, list[str]] = {}
        source_queries_by_ad_library_id: dict[str, list[str]] = {}
        source_origins_by_ad_library_id: dict[str, list[str]] = {}
        matched_user_keywords_by_ad_library_id: dict[str, list[str]] = {}
        technical_diagnostics_by_ad_library_id: dict[str, TechnicalQualification] = {}
        scored_by_ad_library_id: dict[str, dict[str, Any]] = {}
        scoring_state_by_ad_library_id: dict[str, str] = {}
        scoring_attempts_by_ad_library_id: dict[str, int] = {}
        model_scoring_failed_ids: set[str] = set()
        used_query_ids: set[str] = set()
        used_query_keys: set[str] = set()
        used_queries: list[str] = []
        required_user_exact_keys = {
            key
            for keyword in (job.seed_keywords_json or [])
            if (key := _normalized_query_key(keyword))
        }
        executed_user_exact_keys: set[str] = set()
        round_summaries: list[dict[str, Any]] = []
        all_query_metrics: list[dict[str, Any]] = []
        raw_collected = 0
        gap_summary: dict[str, Any] = {}
        termination_reason: str | None = None

        async def score_pairs(
            pairs: list[tuple[CollectorAd, TechnicalQualification]],
        ) -> None:
            if not pairs:
                return
            for ad, _ in pairs:
                scoring_state_by_ad_library_id[ad.ad_library_id] = "scoring"
                scoring_attempts_by_ad_library_id[ad.ad_library_id] = (
                    scoring_attempts_by_ad_library_id.get(ad.ad_library_id, 0) + 1
                )
            scored_pairs = await self._score_candidates(job.category, pairs, job.id)
            for ad, qualification, visual_score, state in scored_pairs:
                ad_library_id = ad.ad_library_id
                if state != "scored" or visual_score is None or qualification.media is None:
                    scoring_state_by_ad_library_id[ad_library_id] = state
                    model_scoring_failed_ids.add(ad_library_id)
                    continue
                technical_diagnostics_by_ad_library_id[ad_library_id] = qualification
                scored_by_ad_library_id[ad_library_id] = _public_result(
                    ad,
                    qualification,
                    visual_score,
                    source_query_ids=source_query_ids_by_ad_library_id[ad_library_id],
                    source_queries=source_queries_by_ad_library_id[ad_library_id],
                    source_query_origins=source_origins_by_ad_library_id[ad_library_id],
                    matched_user_keywords=matched_user_keywords_by_ad_library_id.get(
                        ad_library_id, []
                    ),
                )
                scoring_state_by_ad_library_id[ad_library_id] = "scored"
                model_scoring_failed_ids.discard(ad_library_id)

        for round_number in range(1, MAX_ROUNDS + 1):
            if round_number > 1:
                job.stage = "model_visual_scoring"
                retryable_pairs = [
                    (seen[ad_library_id], technical_diagnostics_by_ad_library_id[ad_library_id])
                    for ad_library_id, state in scoring_state_by_ad_library_id.items()
                    if state == "retryable_failed"
                    and ad_library_id in seen
                    and technical_diagnostics_by_ad_library_id[ad_library_id].qualified
                    and scoring_attempts_by_ad_library_id.get(ad_library_id, 0)
                    < MODEL_SCORE_ATTEMPTS
                ]
                await score_pairs(retryable_pairs)
                _refresh_scored_source_attribution(
                    scored_by_ad_library_id,
                    source_query_ids_by_ad_library_id,
                    source_queries_by_ad_library_id,
                    source_origins_by_ad_library_id,
                    matched_user_keywords_by_ad_library_id,
                )
                retry_scored = sorted(scored_by_ad_library_id.values(), key=_sort_key)
                retry_priority_counts = _priority_counts(retry_scored)
                retry_selected = _select_ranked(retry_scored, job.target_count)
                user_exact_covered = required_user_exact_keys <= executed_user_exact_keys
                if (
                    retry_priority_counts["game_gambling"] >= job.target_count
                    and user_exact_covered
                ):
                    return await self._complete(
                        session=session,
                        job=job,
                        status="completed",
                        selected=retry_selected,
                        raw_collected=raw_collected,
                        deduplicated=len(seen),
                        technical_diagnostics=technical_diagnostics_by_ad_library_id,
                        scored=retry_scored,
                        model_scoring_failed_ids=model_scoring_failed_ids,
                        scoring_states=scoring_state_by_ad_library_id,
                        round_summaries=round_summaries,
                        query_metrics=all_query_metrics,
                    )

            quality_supplement_mode = round_number > STANDARD_ROUNDS
            round_budget = _round_budget(round_number, raw_collected)
            planner_gap_summary = {
                **gap_summary,
                "quality_supplement_mode": quality_supplement_mode,
                "round_budget": round_budget,
            }
            job.current_round = round_number
            job.stage = "planning_queries"
            planned_queries = await self.model.plan_queries(
                country=job.country,
                category=job.category,
                seed_keywords=list(job.seed_keywords_json or []),
                round_number=round_number,
                gap_summary=planner_gap_summary,
            )
            if not planned_queries:
                termination_reason = "query_planning_empty"
                break

            queries, skipped_query_diagnostics = _new_queries(
                planned_queries,
                used_query_ids,
                used_query_keys,
                used_queries,
                round_number=round_number,
            )
            if not queries:
                termination_reason = "query_planning_empty"
                break

            job.stage = "collecting"
            round_raw_collected = 0
            candidates_before_round = len(seen)
            collected_queries = list(queries)
            query_collection_counts: dict[str, dict[str, int]] = {}
            collector_concurrency = max(int(self.settings.ad_research_collector_concurrency), 1)
            semaphore = asyncio.Semaphore(collector_concurrency)

            async def collect_query(
                query: _ResolvedQuery, *, collector_semaphore: asyncio.Semaphore = semaphore
            ) -> tuple[_ResolvedQuery, list[CollectorAd]]:
                async with collector_semaphore:
                    ads = await self.collector.collect(
                        request_id=job.id,
                        query=query.query,
                        country=job.country,
                        limit=PER_QUERY_LIMIT,
                    )
                return query, ads

            collected_batches = await asyncio.gather(*(collect_query(query) for query in queries))
            for query, collected_ads in collected_batches:
                if query.query_origin == "user_exact":
                    executed_key = _normalized_query_key(query.parent_keyword or query.query)
                    if executed_key:
                        executed_user_exact_keys.add(executed_key)
                remaining = max(MAX_RAW_CANDIDATES - raw_collected, 0)
                ads = collected_ads[:remaining]
                raw_count = len(ads)
                new_unique_count = 0
                for ad in ads:
                    if ad.ad_library_id not in seen:
                        seen[ad.ad_library_id] = ad
                        new_unique_count += 1
                    _append_unique(
                        source_query_ids_by_ad_library_id, ad.ad_library_id, query.query_id
                    )
                    _append_unique(
                        source_queries_by_ad_library_id, ad.ad_library_id, query.query
                    )
                    _append_unique(
                        source_origins_by_ad_library_id, ad.ad_library_id, query.query_origin
                    )
                    if query.parent_keyword:
                        _append_unique(
                            matched_user_keywords_by_ad_library_id,
                            ad.ad_library_id,
                            query.parent_keyword,
                        )
                query_collection_counts[query.query_id] = {
                    "raw_collected": raw_count,
                    "new_unique_count": new_unique_count,
                    "duplicate_count": raw_count - new_unique_count,
                }
                raw_collected += raw_count
                round_raw_collected += raw_count

            candidates = list(seen.values())
            job.stage = "technical_filtering"
            uninspected = [
                ad
                for ad in candidates
                if ad.ad_library_id not in technical_diagnostics_by_ad_library_id
            ]
            if uninspected:
                technical_diagnostics_by_ad_library_id.update(
                    await self.media.inspect_many(uninspected, job_id=job.id)
                )
            qualified = [
                ad
                for ad in candidates
                if technical_diagnostics_by_ad_library_id[ad.ad_library_id].qualified
            ]

            job.stage = "model_visual_scoring"
            unscored_pairs = []
            for ad in qualified:
                ad_library_id = ad.ad_library_id
                scoring_state_by_ad_library_id.setdefault(ad_library_id, "pending")
                if (
                    ad_library_id not in scored_by_ad_library_id
                    and scoring_state_by_ad_library_id[ad_library_id] == "pending"
                    and scoring_attempts_by_ad_library_id.get(ad_library_id, 0)
                    < MODEL_SCORE_ATTEMPTS
                ):
                    unscored_pairs.append(
                        (ad, technical_diagnostics_by_ad_library_id[ad_library_id])
                    )
            await score_pairs(unscored_pairs)

            _refresh_scored_source_attribution(
                scored_by_ad_library_id,
                source_query_ids_by_ad_library_id,
                source_queries_by_ad_library_id,
                source_origins_by_ad_library_id,
                matched_user_keywords_by_ad_library_id,
            )
            scored = sorted(scored_by_ad_library_id.values(), key=_sort_key)
            priority_counts = _priority_counts(scored)
            selected = _select_ranked(scored, job.target_count)
            score_distribution = _score_distribution(scored)
            round_budget = _round_budget(round_number, raw_collected)
            quality_summary = _quality_summary(selected)
            progress = {
                "raw_collected": raw_collected,
                "deduplicated": len(candidates),
                "technical_qualified": len(qualified),
                "model_scored": len(scored),
                "model_scoring_failed": len(model_scoring_failed_ids),
                "model_scoring_states": _scoring_state_counts(scoring_state_by_ad_library_id),
                "model_relevant": len(scored),
                "selected_count": len(selected),
                "score_distribution": score_distribution,
                "priority_counts": priority_counts,
                "quality_summary": quality_summary,
                "fallback_count": quality_summary["fallback_count"],
                "query_metrics": [],
                "round_budget": round_budget,
            }

            technical_rejection_summary = _technical_rejection_summary(
                technical_diagnostics_by_ad_library_id
            )
            query_metrics = _query_metrics(
                collected_queries,
                query_collection_counts=query_collection_counts,
                source_query_ids_by_ad_library_id=source_query_ids_by_ad_library_id,
                technical_diagnostics=technical_diagnostics_by_ad_library_id,
                scored_by_ad_library_id=scored_by_ad_library_id,
            )
            all_query_metrics.extend(query_metrics)
            progress["query_metrics"] = list(all_query_metrics)
            job.progress_json = progress
            await session.commit()
            round_new_candidates = len(candidates) - candidates_before_round
            diagnostics = ["no_new_candidates"] if round_new_candidates == 0 else []
            round_summaries.append(
                {
                    **progress,
                    "round": round_number,
                    "quality_supplement_mode": quality_supplement_mode,
                    "queries": [query.query for query in collected_queries],
                    "planned_query_count": len(planned_queries),
                    "skipped_query_count": len(planned_queries) - len(queries),
                    "skipped_query_diagnostics": skipped_query_diagnostics,
                    "round_raw_collected": round_raw_collected,
                    "round_new_candidates": round_new_candidates,
                    "diagnostics": diagnostics,
                    "query_metrics": query_metrics,
                    "priority_counts": priority_counts,
                    "quality_summary": quality_summary,
                    "fallback_count": quality_summary["fallback_count"],
                    "round_budget": round_budget,
                    "technical_rejection_summary": technical_rejection_summary,
                }
            )
            gap_summary = {
                "target_count": job.target_count,
                "missing_count": max(job.target_count - len(scored), 0),
                "priority_counts": priority_counts,
                "priority_gap_counts": _priority_gap_counts(priority_counts, job.target_count),
                "priority_gaps": _quality_supplement_signals(priority_counts, job.target_count),
                "missing_play_patterns": _quality_supplement_signals(
                    priority_counts, job.target_count
                ),
                "query_metrics": query_metrics,
                "query_performance": _planner_query_performance(query_metrics),
                "previous_queries": list(used_queries),
                "technical_rejection_summary": technical_rejection_summary,
                "model_scoring_failed": len(model_scoring_failed_ids),
                "model_scoring_states": _scoring_state_counts(scoring_state_by_ad_library_id),
                "duplicate_count": raw_collected - len(candidates),
                "skipped_query_count": len(planned_queries) - len(queries),
                "skipped_query_diagnostics": skipped_query_diagnostics,
            }

            has_scored_target = len(scored) >= job.target_count
            has_p1_target = priority_counts["game_gambling"] >= job.target_count
            user_exact_covered = required_user_exact_keys <= executed_user_exact_keys
            if has_p1_target and user_exact_covered:
                return await self._complete(
                    session=session,
                    job=job,
                    status="completed",
                    selected=selected,
                    raw_collected=raw_collected,
                    deduplicated=len(candidates),
                    technical_diagnostics=technical_diagnostics_by_ad_library_id,
                    scored=scored,
                    model_scoring_failed_ids=model_scoring_failed_ids,
                    scoring_states=scoring_state_by_ad_library_id,
                    round_summaries=round_summaries,
                    query_metrics=all_query_metrics,
                )

            if round_number == MAX_ROUNDS or raw_collected >= MAX_RAW_CANDIDATES:
                return await self._complete(
                    session=session,
                    job=job,
                    status="completed" if has_scored_target else "failed",
                    selected=selected if has_scored_target else [],
                    raw_collected=raw_collected,
                    deduplicated=len(candidates),
                    technical_diagnostics=technical_diagnostics_by_ad_library_id,
                    scored=scored,
                    model_scoring_failed_ids=model_scoring_failed_ids,
                    scoring_states=scoring_state_by_ad_library_id,
                    round_summaries=round_summaries,
                    query_metrics=all_query_metrics,
                    reason=None if has_scored_target else "insufficient_qualified_ads",
                )

        _refresh_scored_source_attribution(
            scored_by_ad_library_id,
            source_query_ids_by_ad_library_id,
            source_queries_by_ad_library_id,
            source_origins_by_ad_library_id,
            matched_user_keywords_by_ad_library_id,
        )
        scored = sorted(scored_by_ad_library_id.values(), key=_sort_key)
        return await self._complete(
            session=session,
            job=job,
            status="failed",
            selected=[],
            raw_collected=raw_collected,
            deduplicated=len(seen),
            technical_diagnostics=technical_diagnostics_by_ad_library_id,
            scored=scored,
            model_scoring_failed_ids=model_scoring_failed_ids,
            scoring_states=scoring_state_by_ad_library_id,
            round_summaries=round_summaries,
            query_metrics=all_query_metrics,
            reason=termination_reason or "insufficient_qualified_ads",
        )

    async def _score_candidates(
        self,
        category: str,
        candidates: list[tuple[CollectorAd, TechnicalQualification]],
        job_id: str,
    ) -> list[tuple[CollectorAd, TechnicalQualification, dict[str, Any] | None, str]]:
        semaphore = asyncio.Semaphore(max(int(self.settings.ad_research_model_concurrency), 1))

        async def call_model(
            media_duration_seconds: float, media: PreparedAdMedia
        ) -> dict[str, Any]:
            async with semaphore:
                return await self.model.score_visual(
                    category=category,
                    duration_seconds=float(media_duration_seconds),
                    media=media,
                )

        async def score_one(
            candidate: CollectorAd, qualification: TechnicalQualification
        ) -> tuple[CollectorAd, TechnicalQualification, dict[str, Any] | None, str]:
            media = qualification.media
            if media is None:
                return candidate, qualification, None, "permanent_failed"

            try:
                score = await call_model(qualification.duration_seconds, media)
            except Exception as first_error:
                if _is_permanent_scoring_error(first_error):
                    return candidate, qualification, None, "permanent_failed"
                try:
                    retry_score = await call_model(qualification.duration_seconds, media)
                except Exception as retry_error:
                    state = (
                        "permanent_failed"
                        if _is_permanent_scoring_error(retry_error)
                        else "retryable_failed"
                    )
                    return candidate, qualification, None, state
                return candidate, qualification, retry_score, "scored"

            confidence = float(score.get("analysis_confidence") or 0)
            if confidence >= LOW_CONFIDENCE_THRESHOLD:
                return candidate, qualification, score, "scored"

            try:
                enriched = await self.media.add_low_confidence_frames(
                    candidate, qualification, job_id=job_id
                )
            except Exception:
                return candidate, qualification, score, "scored"
            if enriched.media is None:
                return candidate, qualification, score, "scored"
            try:
                rescored = await call_model(enriched.duration_seconds, enriched.media)
            except Exception:
                # A low-confidence follow-up preserves the successful first score. It is not
                # an independent retry budget beyond the original successful score.
                return candidate, qualification, score, "scored"
            return candidate, enriched, rescored, "scored"

        return await asyncio.gather(
            *(score_one(candidate, qualification) for candidate, qualification in candidates)
        )

    async def _complete(
        self,
        *,
        session: AsyncSession,
        job: AdResearchJob,
        status: str,
        selected: list[dict[str, Any]],
        raw_collected: int,
        deduplicated: int,
        technical_diagnostics: dict[str, TechnicalQualification],
        scored: list[dict[str, Any]],
        model_scoring_failed_ids: set[str],
        scoring_states: dict[str, str],
        round_summaries: list[dict[str, Any]],
        query_metrics: list[dict[str, Any]],
        reason: str | None = None,
    ) -> AdResearchRunResult:
        completed_selected = selected if status == "completed" else []
        if status == "completed":
            await self.media.retain_only(
                job.id, {ad["ad_library_id"] for ad in completed_selected}
            )
        else:
            await self.media.cleanup_job_media(job.id)
        summary = _summary(
            raw=raw_collected,
            deduplicated=deduplicated,
            technical_diagnostics=technical_diagnostics,
            scored=scored,
            selected=completed_selected,
            model_scoring_failed_ids=model_scoring_failed_ids,
            scoring_states=scoring_states,
            round_summaries=round_summaries,
            query_metrics=query_metrics,
            reason=reason,
        )
        quality_summary = summary["quality_summary"]
        job.progress_json = {
            **(job.progress_json or {}),
            "priority_counts": summary["priority_counts"],
            "quality_summary": quality_summary,
            "query_metrics": summary["query_metrics"],
            "fallback_count": quality_summary["fallback_count"],
            "round_budget": summary["round_budget"],
            "selected_count": len(completed_selected),
        }
        await self.service.complete_job(
            session,
            job,
            status=status,
            ads=completed_selected,
            summary=summary,
        )
        return AdResearchRunResult(status, completed_selected, summary)

    async def execute_task(self, session: AsyncSession, task: GenerationTask) -> None:
        job = await session.get(AdResearchJob, task.business_id)
        if job is None or job.status in {"completed", "insufficient", "expired"}:
            return
        task.status = "running"
        task.started_at = utcnow()
        task.attempt_count += 1
        await session.commit()
        try:
            result = await self.run(session, job)
        except Exception as exc:
            await self.media.cleanup_job_media(job.id)
            await self.service.fail_job(session, job, exc)
            task.status = "failed"
            task.error_code = "ad_research_failed"
            task.error_message = str(exc)[:300]
            task.finished_at = utcnow()
            await session.commit()
            raise
        else:
            task.status = "succeeded" if result.status == "completed" else "failed"
            task.result_json = {"status": result.status, "selected_count": len(result.ads)}
            task.error_code = None if result.status == "completed" else "insufficient_qualified_ads"
            task.finished_at = utcnow()
            await session.commit()


def _normalized_query_key(value: Any) -> str:
    return " ".join(str(value).split()).casefold() if value is not None else ""


def _new_queries(
    planned_queries: QueryPlan | list[str],
    used_query_ids: set[str],
    used_query_keys: set[str],
    used_queries: list[str],
    *,
    round_number: int,
) -> tuple[list[_ResolvedQuery], list[dict[str, str]]]:
    is_structured_plan = isinstance(planned_queries, QueryPlan)
    source_queries = (
        (
            _ResolvedQuery(
                query_id=planned_query.query_id,
                query=planned_query.query,
                intent=planned_query.intent,
                query_origin=planned_query.query_origin,
                parent_keyword=planned_query.parent_keyword,
            )
            for planned_query in planned_queries.queries
        )
        if is_structured_plan
        else (
            _ResolvedQuery(
                query_id=f"legacy_r{round_number}_q{index:02d}",
                query=str(query),
                intent="legacy",
            )
            for index, query in enumerate(planned_queries, start=1)
        )
    )
    queries: list[_ResolvedQuery] = []
    skipped_diagnostics: list[dict[str, str]] = []
    expected_query_id_prefix = f"r{round_number}_q"
    for planned_query in source_queries:
        query_id_key = planned_query.query_id.casefold()
        if query_id_key in used_query_ids:
            skipped_diagnostics.append(
                {"query_id": planned_query.query_id, "reason": "query_id_already_used"}
            )
            continue
        if is_structured_plan and not query_id_key.startswith(expected_query_id_prefix):
            skipped_diagnostics.append(
                {"query_id": planned_query.query_id, "reason": "query_id_wrong_round"}
            )
            continue

        normalized = " ".join(planned_query.query.split())
        query_key = normalized.casefold()
        if not normalized:
            skipped_diagnostics.append(
                {"query_id": planned_query.query_id, "reason": "query_empty"}
            )
            continue
        if query_key in used_query_keys:
            skipped_diagnostics.append(
                {"query_id": planned_query.query_id, "reason": "query_already_used"}
            )
            continue

        used_query_ids.add(query_id_key)
        used_query_keys.add(query_key)
        used_queries.append(normalized)
        queries.append(
            _ResolvedQuery(
                query_id=planned_query.query_id,
                query=normalized,
                intent=planned_query.intent,
                query_origin=planned_query.query_origin,
                parent_keyword=planned_query.parent_keyword,
            )
        )
    return queries, skipped_diagnostics


def _append_unique(mapping: dict[str, list[str]], key: str, value: str) -> None:
    values = mapping.setdefault(key, [])
    if value not in values:
        values.append(value)


def _refresh_scored_source_attribution(
    scored_by_ad_library_id: dict[str, dict[str, Any]],
    source_query_ids_by_ad_library_id: dict[str, list[str]],
    source_queries_by_ad_library_id: dict[str, list[str]],
    source_origins_by_ad_library_id: dict[str, list[str]],
    matched_user_keywords_by_ad_library_id: dict[str, list[str]],
) -> None:
    for ad_library_id, scored in scored_by_ad_library_id.items():
        source_query_ids = source_query_ids_by_ad_library_id.get(ad_library_id, [])
        source_queries = source_queries_by_ad_library_id.get(ad_library_id, [])
        scored["first_source_query_id"] = source_query_ids[0] if source_query_ids else None
        scored["source_query_ids"] = list(source_query_ids)
        scored["source_query"] = source_queries[0] if source_queries else None
        scored["matched_queries"] = list(source_queries)
        scored["matched_user_keywords"] = list(
            matched_user_keywords_by_ad_library_id.get(ad_library_id, [])
        )
        scored["matched_query_origins"] = list(
            source_origins_by_ad_library_id.get(ad_library_id, [])
        )


def _public_result(
    ad: CollectorAd,
    qualification: TechnicalQualification,
    visual_score: dict[str, Any],
    *,
    source_query_ids: list[str],
    source_queries: list[str],
    source_query_origins: list[str],
    matched_user_keywords: list[str],
) -> dict[str, Any]:
    media = qualification.media
    if media is None:
        raise ValueError("qualified ad research candidate has no prepared media")
    visual_score = _validated_visual_score(
        visual_score,
        frame_count=len(media.local_frame_paths),
    )
    visual_total = float(visual_score["visual_total"])
    return {
        "ad_library_id": ad.ad_library_id,
        "first_source_query_id": source_query_ids[0],
        "source_query_ids": list(source_query_ids),
        "source_query": source_queries[0],
        "matched_queries": list(source_queries),
        "matched_user_keywords": list(matched_user_keywords),
        "matched_query_origins": list(source_query_origins),
        "advertiser_name": ad.advertiser_name,
        "ad_snapshot_url": ad.ad_snapshot_url,
        "text": "\n".join(ad.text_variants[:3]),
        "headline": ad.headline,
        "cta_text": ad.cta_text,
        "video_url": ad.video_url,
        "thumbnail_url": ad.thumbnail_url,
        "duration_seconds": qualification.duration_seconds,
        "active_days": qualification.active_days,
        "platforms": ad.platforms,
        "final_score": visual_total,
        "visual_total": visual_total,
        "visual_priority": visual_score["visual_priority"],
        "game_context_present": visual_score["game_context_present"],
        "betting_context_present": visual_score["betting_context_present"],
        "money_only_promo": visual_score["money_only_promo"],
        "negative_visual_type": visual_score["negative_visual_type"],
        "component_scores": dict(visual_score["component_scores"]),
        "analysis_confidence": float(visual_score["analysis_confidence"]),
        "visual_evidence": list(visual_score["visual_evidence"]),
        "retrieval_hints": list(visual_score["retrieval_hints"]),
        "is_fallback": False,
        "fallback_reason": None,
        "media": _public_media(media),
    }

def _public_media(media: PreparedAdMedia) -> dict[str, Any]:
    return {
        "cover_url": media.cover_url,
        "cover_source": media.cover_source,
        "frame_urls": list(media.frame_urls),
        "frame_count": len(media.frame_urls),
        "duration_source": media.duration_source,
        "duration_probe_attempts": media.duration_probe_attempts,
    }


def _sort_key(ad: dict[str, Any]) -> tuple[Any, ...]:
    """Compatibility wrapper around the shared visual-only ordering contract."""
    return visual_sort_key(ad)


def _select_ranked(scored: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    """Compatibility wrapper around deterministic selection and P4 labeling."""
    return select_ranked(scored, target_count)


def _quality_summary(
    selected: list[dict[str, Any]], *, target_count: int | None = None
) -> dict[str, int | bool | str]:
    """Compatibility wrapper around shared visual-quality summary fields."""
    return build_quality_summary(
        selected,
        target_count=len(selected) if target_count is None else target_count,
    )


def _score_distribution(scored: list[dict[str, Any]]) -> dict[str, int]:
    distribution = {"0_19": 0, "20_39": 0, "40_54": 0, "55_69": 0, "70_89": 0, "90_100": 0}
    for candidate in scored:
        score = float(candidate["visual_total"])
        if score < 20:
            distribution["0_19"] += 1
        elif score < 40:
            distribution["20_39"] += 1
        elif score < 55:
            distribution["40_54"] += 1
        elif score < 70:
            distribution["55_69"] += 1
        elif score < 90:
            distribution["70_89"] += 1
        else:
            distribution["90_100"] += 1
    return distribution


def _high_score_visible_elements(scored: list[dict[str, Any]]) -> list[str]:
    elements: list[str] = []
    seen: set[str] = set()
    for candidate in scored:
        if float(candidate["visual_total"]) < 55:
            continue
        for item in candidate.get("retrieval_hints") or []:
            element = str(item).strip()
            key = element.casefold()
            if not element or key in seen:
                continue
            seen.add(key)
            elements.append(element)
            if len(elements) == 24:
                return elements
    return elements


def _priority_counts(scored: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "game_gambling": 0,
        "sports_betting": 0,
        "gambling_adjacent": 0,
        "unrelated": 0,
    }
    for candidate in scored:
        priority = str(candidate.get("visual_priority") or "unrelated")
        counts[priority if priority in counts else "unrelated"] += 1
    return counts


def _query_metrics(
    queries: list[_ResolvedQuery],
    *,
    query_collection_counts: dict[str, dict[str, int]],
    source_query_ids_by_ad_library_id: dict[str, list[str]],
    technical_diagnostics: dict[str, TechnicalQualification],
    scored_by_ad_library_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for query in sorted(queries, key=lambda item: item.query_id):
        attributed_ad_ids = [
            ad_library_id
            for ad_library_id, source_query_ids in source_query_ids_by_ad_library_id.items()
            if query.query_id in source_query_ids
        ]
        qualifications = [
            technical_diagnostics[ad_library_id]
            for ad_library_id in attributed_ad_ids
            if ad_library_id in technical_diagnostics
        ]
        scored = [
            scored_by_ad_library_id[ad_library_id]
            for ad_library_id in attributed_ad_ids
            if ad_library_id in scored_by_ad_library_id
        ]
        priority_counts = _priority_counts(scored)
        visual_scores = [float(candidate["visual_total"]) for candidate in scored]
        collection_counts = query_collection_counts[query.query_id]
        metrics.append(
            {
                "query_id": query.query_id,
                "query": query.query,
                "intent": query.intent,
                **collection_counts,
                "duration_le_30_count": sum(
                    qualification.duration_seconds is not None
                    and qualification.duration_seconds <= MAX_VIDEO_SECONDS
                    for qualification in qualifications
                ),
                "technical_qualified": sum(
                    qualification.qualified for qualification in qualifications
                ),
                "model_scored": len(scored),
                "game_gambling_count": priority_counts["game_gambling"],
                "sports_betting_count": priority_counts["sports_betting"],
                "gambling_adjacent_count": priority_counts["gambling_adjacent"],
                "unrelated_count": priority_counts["unrelated"],
                "score_above_55": sum(score > 55 for score in visual_scores),
                "average_visual_score": (
                    round(sum(visual_scores) / len(visual_scores), 2) if visual_scores else None
                ),
                "best_visual_score": max(visual_scores) if visual_scores else None,
            }
        )
    return metrics


def _planner_query_performance(query_metrics: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "query_id": metric["query_id"],
            "query": metric["query"],
            "collected_count": metric["raw_collected"],
            "selected_count": metric["model_scored"],
            "rejected_count": metric["raw_collected"] - metric["model_scored"],
        }
        for metric in query_metrics
    ]


def _technical_rejection_summary(
    diagnostics_by_ad_library_id: dict[str, TechnicalQualification],
) -> dict[str, int]:
    summary: dict[str, int] = {}
    for diagnostic in diagnostics_by_ad_library_id.values():
        for reason in diagnostic.reasons:
            summary[reason] = summary.get(reason, 0) + 1
    return summary


def _round_budget(round_number: int, raw_collected: int) -> dict[str, int | bool]:
    current_round = max(round_number, 0)
    return {
        "standard_rounds": STANDARD_ROUNDS,
        "max_rounds": MAX_ROUNDS,
        "current_round": current_round,
        "remaining_rounds": max(MAX_ROUNDS - current_round, 0),
        "max_raw_candidates": MAX_RAW_CANDIDATES,
        "remaining_raw_candidates": max(MAX_RAW_CANDIDATES - raw_collected, 0),
        "quality_supplement_mode": current_round > STANDARD_ROUNDS,
    }


def _priority_gap_counts(priority_counts: dict[str, int], target_count: int) -> dict[str, int]:
    return {
        priority: max(target_count - count, 0)
        for priority, count in priority_counts.items()
        if priority != "unrelated"
    }


def _quality_supplement_signals(
    priority_counts: dict[str, int], target_count: int
) -> list[str]:
    signals: list[str] = []
    for priority in ("game_gambling", "sports_betting", "gambling_adjacent"):
        if priority_counts.get(priority, 0) >= target_count:
            continue
        for signal in _QUALITY_SUPPLEMENT_SIGNALS[priority]:
            if signal not in signals:
                signals.append(signal)
    return signals



def _is_permanent_scoring_error(error: Exception) -> bool:
    return (
        isinstance(error, ProviderError)
        and "permanent failure" in str(error).casefold()
    )


def _scoring_state_counts(states: dict[str, str]) -> dict[str, int]:
    return {
        state: sum(value == state for value in states.values())
        for state in (
            "pending",
            "scoring",
            "retryable_failed",
            "scored",
            "permanent_failed",
        )
    }


def _summary(
    *,
    raw: int,
    deduplicated: int,
    technical_diagnostics: dict[str, TechnicalQualification],
    scored: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    model_scoring_failed_ids: set[str],
    scoring_states: dict[str, str],
    round_summaries: list[dict[str, Any]],
    query_metrics: list[dict[str, Any]],
    reason: str | None,
) -> dict[str, Any]:
    priority_counts = _priority_counts(scored)
    quality_summary = _quality_summary(selected)
    current_round = int(round_summaries[-1]["round"]) if round_summaries else 0
    summary = {
        "raw_collected": raw,
        "deduplicated": deduplicated,
        "technical_qualified": sum(
            diagnostic.qualified for diagnostic in technical_diagnostics.values()
        ),
        "model_scored": len(scored),
        "model_scoring_failed": len(model_scoring_failed_ids),
        "model_scoring_states": _scoring_state_counts(scoring_states),
        "model_relevant": len(scored),
        "selected_count": len(selected),
        "minimum_active_days": MIN_ACTIVE_DAYS,
        "maximum_video_seconds": MAX_VIDEO_SECONDS,
        "score_distribution": _score_distribution(scored),
        "high_score_visible_elements": _high_score_visible_elements(scored),
        "priority_counts": priority_counts,
        "quality_summary": quality_summary,
        "fallback_count": quality_summary["fallback_count"],
        "round_budget": _round_budget(current_round, raw),
        "technical_rejection_summary": _technical_rejection_summary(technical_diagnostics),
        "rounds": round_summaries,
        "query_metrics": sorted(query_metrics, key=lambda item: str(item["query_id"])),
        "model_relevant_notice": (
            "Compatibility field only: model_relevant equals model_scored and no longer means "
            "a text or category hard match."
        ),
    }
    if reason is not None:
        summary["reason"] = reason
    return summary
