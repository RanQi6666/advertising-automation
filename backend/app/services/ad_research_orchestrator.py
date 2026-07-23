from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
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
from backend.app.services.ad_research_service import AdResearchService

MAX_ROUNDS = 4
MAX_RAW_CANDIDATES = 500
PER_QUERY_LIMIT = 50
LOW_CONFIDENCE_THRESHOLD = 0.60


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
        technical_diagnostics_by_ad_library_id: dict[str, TechnicalQualification] = {}
        scored_by_ad_library_id: dict[str, dict[str, Any]] = {}
        model_scoring_failed_ids: set[str] = set()
        used_query_ids: set[str] = set()
        used_query_keys: set[str] = set()
        used_queries: list[str] = []
        round_summaries: list[dict[str, Any]] = []
        all_query_metrics: list[dict[str, Any]] = []
        raw_collected = 0
        gap_summary: dict[str, Any] = {}
        termination_reason: str | None = None

        for round_number in range(1, MAX_ROUNDS + 1):
            job.current_round = round_number
            job.stage = "planning_queries"
            planned_queries = await self.model.plan_queries(
                country=job.country,
                category=job.category,
                seed_keywords=list(job.seed_keywords_json or []),
                round_number=round_number,
                gap_summary=gap_summary,
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
            job.stage = "collecting"
            round_raw_collected = 0
            candidates_before_round = len(seen)
            collected_queries: list[_ResolvedQuery] = []
            query_collection_counts: dict[str, dict[str, int]] = {}
            for query in queries:
                remaining = MAX_RAW_CANDIDATES - raw_collected
                if remaining <= 0:
                    break
                ads = await self.collector.collect(
                    request_id=job.id,
                    query=query.query,
                    country=job.country,
                    limit=min(PER_QUERY_LIMIT, remaining),
                )
                collected_queries.append(query)
                raw_count = len(ads)
                new_unique_count = 0
                for ad in ads:
                    if ad.ad_library_id not in seen:
                        seen[ad.ad_library_id] = ad
                        new_unique_count += 1
                    source_query_ids = source_query_ids_by_ad_library_id.setdefault(
                        ad.ad_library_id, []
                    )
                    if query.query_id not in source_query_ids:
                        source_query_ids.append(query.query_id)
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
            unscored_pairs = [
                (ad, technical_diagnostics_by_ad_library_id[ad.ad_library_id])
                for ad in qualified
                if ad.ad_library_id not in scored_by_ad_library_id
                and ad.ad_library_id not in model_scoring_failed_ids
            ]
            scored_pairs = await self._score_candidates(job.category, unscored_pairs, job.id)
            for ad, qualification, visual_score, model_failed in scored_pairs:
                if model_failed or visual_score is None or qualification.media is None:
                    model_scoring_failed_ids.add(ad.ad_library_id)
                    continue
                technical_diagnostics_by_ad_library_id[ad.ad_library_id] = qualification
                scored_by_ad_library_id[ad.ad_library_id] = _public_result(
                    ad,
                    qualification,
                    visual_score,
                    source_query_ids=source_query_ids_by_ad_library_id[ad.ad_library_id],
                )

            _refresh_scored_source_attribution(
                scored_by_ad_library_id, source_query_ids_by_ad_library_id
            )
            scored = sorted(scored_by_ad_library_id.values(), key=_sort_key)
            selected = _select_ranked(scored, job.target_count)
            score_distribution = _score_distribution(scored)
            progress = {
                "raw_collected": raw_collected,
                "deduplicated": len(candidates),
                "technical_qualified": len(qualified),
                "model_scored": len(scored),
                "model_scoring_failed": len(model_scoring_failed_ids),
                "model_relevant": len(scored),
                "selected_count": len(selected),
                "score_distribution": score_distribution,
                "quality_summary": _quality_summary(selected),
            }
            job.progress_json = progress
            await session.commit()

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
            priority_counts = _priority_counts(scored)
            round_new_candidates = len(candidates) - candidates_before_round
            round_summaries.append(
                {
                    "round": round_number,
                    "queries": [query.query for query in collected_queries],
                    "planned_query_count": len(planned_queries),
                    "skipped_query_count": len(planned_queries) - len(queries),
                    "skipped_query_diagnostics": skipped_query_diagnostics,
                    "round_raw_collected": round_raw_collected,
                    "round_new_candidates": round_new_candidates,
                    "query_metrics": query_metrics,
                    "priority_counts": priority_counts,
                    **progress,
                    "technical_rejection_summary": technical_rejection_summary,
                }
            )
            gap_summary = {
                "target_count": job.target_count,
                "missing_count": max(job.target_count - len(scored), 0),
                "priority_counts": priority_counts,
                "query_metrics": query_metrics,
                "query_performance": _planner_query_performance(query_metrics),
                "previous_queries": list(used_queries),
                "technical_rejection_summary": technical_rejection_summary,
                "model_scoring_failed": len(model_scoring_failed_ids),
                "duplicate_count": raw_collected - len(candidates),
                "skipped_query_count": len(planned_queries) - len(queries),
                "skipped_query_diagnostics": skipped_query_diagnostics,
            }

            if len(scored) >= job.target_count:
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
                    round_summaries=round_summaries,
                    query_metrics=all_query_metrics,
                )
            if round_number == MAX_ROUNDS or raw_collected >= MAX_RAW_CANDIDATES:
                termination_reason = "insufficient_qualified_ads"
                break
            if round_new_candidates == 0:
                termination_reason = "no_new_candidates"
                break

        _refresh_scored_source_attribution(
            scored_by_ad_library_id, source_query_ids_by_ad_library_id
        )
        scored = sorted(scored_by_ad_library_id.values(), key=_sort_key)
        selected = _select_ranked(scored, job.target_count)
        status = "completed" if len(scored) >= job.target_count else "insufficient"
        return await self._complete(
            session=session,
            job=job,
            status=status,
            selected=selected,
            raw_collected=raw_collected,
            deduplicated=len(seen),
            technical_diagnostics=technical_diagnostics_by_ad_library_id,
            scored=scored,
            model_scoring_failed_ids=model_scoring_failed_ids,
            round_summaries=round_summaries,
            query_metrics=all_query_metrics,
            reason=termination_reason or "insufficient_qualified_ads",
        )

    async def _score_candidates(
        self,
        category: str,
        candidates: list[tuple[CollectorAd, TechnicalQualification]],
        job_id: str,
    ) -> list[tuple[CollectorAd, TechnicalQualification, dict[str, Any] | None, bool]]:
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
        ) -> tuple[CollectorAd, TechnicalQualification, dict[str, Any] | None, bool]:
            media = qualification.media
            if media is None:
                return candidate, qualification, None, True
            try:
                score = await call_model(qualification.duration_seconds, media)
            except Exception:
                return candidate, qualification, None, True

            if float(score.get("analysis_confidence") or 0) >= LOW_CONFIDENCE_THRESHOLD:
                return candidate, qualification, score, False

            try:
                enriched = await self.media.add_low_confidence_frames(
                    candidate, qualification, job_id=job_id
                )
            except Exception:
                return candidate, qualification, score, False
            if enriched.media is None:
                return candidate, qualification, score, False
            try:
                rescored = await call_model(enriched.duration_seconds, enriched.media)
            except Exception:
                return candidate, qualification, score, False
            return candidate, enriched, rescored, False

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
        round_summaries: list[dict[str, Any]],
        query_metrics: list[dict[str, Any]],
        reason: str | None = None,
    ) -> AdResearchRunResult:
        await self.media.retain_only(job.id, {ad["ad_library_id"] for ad in selected})
        summary = _summary(
            raw=raw_collected,
            deduplicated=deduplicated,
            technical_diagnostics=technical_diagnostics,
            scored=scored,
            selected=selected,
            model_scoring_failed_ids=model_scoring_failed_ids,
            round_summaries=round_summaries,
            query_metrics=query_metrics,
            reason=reason,
        )
        await self.service.complete_job(session, job, status=status, ads=selected, summary=summary)
        return AdResearchRunResult(status, selected, summary)

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
            )
        )
    return queries, skipped_diagnostics


def _refresh_scored_source_attribution(
    scored_by_ad_library_id: dict[str, dict[str, Any]],
    source_query_ids_by_ad_library_id: dict[str, list[str]],
) -> None:
    for ad_library_id, scored in scored_by_ad_library_id.items():
        source_query_ids = source_query_ids_by_ad_library_id[ad_library_id]
        scored["first_source_query_id"] = source_query_ids[0]
        scored["source_query_ids"] = list(source_query_ids)


def _public_result(
    ad: CollectorAd,
    qualification: TechnicalQualification,
    visual_score: dict[str, Any],
    *,
    source_query_ids: list[str],
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
        "visual_priority": str(visual_score.get("visual_priority") or "unrelated"),
        "gameplay_gambling_points": float(visual_score.get("gameplay_gambling_points") or 0),
        "multi_signal_style_points": float(visual_score.get("multi_signal_style_points") or 0),
        "betting_mechanism_points": float(visual_score.get("betting_mechanism_points") or 0),
        "gambling_visual_style_points": float(
            visual_score.get("gambling_visual_style_points") or 0
        ),
        "visual_clarity_points": float(visual_score.get("visual_clarity_points") or 0),
        "media_quality_points": float(visual_score.get("media_quality_points") or 0),
        "analysis_confidence": float(visual_score.get("analysis_confidence") or 0),
        "gambling_signals": list(visual_score.get("gambling_signals") or []),
        "game_visual_present": bool(visual_score.get("game_visual_present")),
        "visual_evidence": list(visual_score.get("visual_evidence") or []),
        "retrieval_hints": list(visual_score.get("retrieval_hints") or []),
        "uncertain": bool(visual_score.get("uncertain")),
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


def _sort_key(ad: dict[str, Any]) -> tuple[int, float, float, int, int, str]:
    priority_rank = {
        "game_gambling": 0,
        "sports_betting": 1,
        "gambling_adjacent": 2,
        "unrelated": 3,
    }
    priority = str(ad.get("visual_priority") or "unrelated")
    return (
        priority_rank.get(priority, priority_rank["unrelated"]),
        -float(ad["visual_total"]),
        -float(ad.get("analysis_confidence") or 0),
        -int(ad.get("active_days") or 0),
        -int(ad["media"]["frame_count"]),
        str(ad["ad_library_id"]),
    )


def _select_ranked(scored: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for candidate in scored[: max(target_count, 0)]:
        is_fallback = str(candidate.get("visual_priority") or "unrelated") == "unrelated"
        selected.append(
            {
                **candidate,
                "is_fallback": is_fallback,
                "fallback_reason": (
                    "insufficient_high_relevance_candidates" if is_fallback else None
                ),
            }
        )
    return selected


def _quality_summary(selected: list[dict[str, Any]]) -> dict[str, int | bool]:
    priority_counts = _priority_counts(selected)
    fallback_count = sum(bool(candidate.get("is_fallback")) for candidate in selected)
    return {
        "game_gambling_count": priority_counts["game_gambling"],
        "sports_betting_count": priority_counts["sports_betting"],
        "gambling_adjacent_count": priority_counts["gambling_adjacent"],
        "fallback_count": fallback_count,
        "fallback_used": fallback_count > 0,
    }


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
        for item in candidate.get("gambling_signals") or []:
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
                    qualification.duration_seconds <= MAX_VIDEO_SECONDS
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


def _summary(
    *,
    raw: int,
    deduplicated: int,
    technical_diagnostics: dict[str, TechnicalQualification],
    scored: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    model_scoring_failed_ids: set[str],
    round_summaries: list[dict[str, Any]],
    query_metrics: list[dict[str, Any]],
    reason: str | None,
) -> dict[str, Any]:
    summary = {
        "raw_collected": raw,
        "deduplicated": deduplicated,
        "technical_qualified": sum(
            diagnostic.qualified for diagnostic in technical_diagnostics.values()
        ),
        "model_scored": len(scored),
        "model_scoring_failed": len(model_scoring_failed_ids),
        "model_relevant": len(scored),
        "selected_count": len(selected),
        "minimum_active_days": MIN_ACTIVE_DAYS,
        "maximum_video_seconds": MAX_VIDEO_SECONDS,
        "score_distribution": _score_distribution(scored),
        "high_score_visible_elements": _high_score_visible_elements(scored),
        "quality_summary": _quality_summary(selected),
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
