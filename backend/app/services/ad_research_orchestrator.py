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
    TechnicalQualification,
)
from backend.app.services.ad_research_model import AdResearchModel
from backend.app.services.ad_research_service import AdResearchService

MAX_ROUNDS = 4
MAX_RAW_CANDIDATES = 500
PER_QUERY_LIMIT = 50
MIN_CATEGORY_CONFIDENCE = 0.85


@dataclass(frozen=True)
class AdResearchRunResult:
    status: str
    ads: list[dict[str, Any]]
    summary: dict[str, Any]


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
        technical_diagnostics_by_ad_library_id: dict[str, TechnicalQualification] = {}
        scored_by_ad_library_id: dict[str, dict[str, Any]] = {}
        classification_diagnostics_by_ad_library_id: dict[str, dict[str, Any]] = {}
        used_query_keys: set[str] = set()
        used_queries: list[str] = []
        round_summaries: list[dict[str, Any]] = []
        scored: list[dict[str, Any]] = []
        raw_collected = 0
        technical_qualified = 0
        gap_summary: dict[str, Any] = {}

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
                break

            queries: list[str] = []
            for query in planned_queries:
                normalized = " ".join(str(query).split())
                query_key = normalized.casefold()
                if not normalized or query_key in used_query_keys:
                    continue
                used_query_keys.add(query_key)
                used_queries.append(normalized)
                queries.append(normalized)

            job.stage = "collecting"
            round_raw_collected = 0
            candidates_before_round = len(seen)
            for query in queries:
                remaining = MAX_RAW_CANDIDATES - raw_collected
                if remaining <= 0:
                    break
                ads = await self.collector.collect(
                    request_id=job.id,
                    query=query,
                    country=job.country,
                    limit=min(PER_QUERY_LIMIT, remaining),
                )
                raw_collected += len(ads)
                round_raw_collected += len(ads)
                for ad in ads:
                    seen.setdefault(ad.ad_library_id, ad)

            candidates = list(seen.values())
            job.stage = "technical_filtering"
            for ad in candidates:
                if ad.ad_library_id not in technical_diagnostics_by_ad_library_id:
                    technical_diagnostics_by_ad_library_id[
                        ad.ad_library_id
                    ] = await self.media.inspect(ad)
            qualified = [
                ad
                for ad in candidates
                if technical_diagnostics_by_ad_library_id[ad.ad_library_id].qualified
            ]
            technical_qualified = len(qualified)
            scored = sorted(scored_by_ad_library_id.values(), key=_sort_key, reverse=True)
            job.progress_json = {
                "raw_collected": raw_collected,
                "deduplicated": len(candidates),
                "technical_qualified": technical_qualified,
                "model_relevant": len(scored),
                "selected_count": min(len(scored), job.target_count),
            }
            await session.commit()

            job.stage = "model_classifying"
            unclassified = [
                ad
                for ad in qualified
                if ad.ad_library_id not in classification_diagnostics_by_ad_library_id
            ]
            classifications = await self._classify_candidates(job.category, unclassified)
            classification_failures = 0
            for ad, classification in zip(unclassified, classifications, strict=True):
                if isinstance(classification, Exception):
                    classification_failures += 1
                    continue
                classification_diagnostics_by_ad_library_id[ad.ad_library_id] = (
                    _classification_diagnostic(ad, classification)
                )
                if _keep(classification):
                    scored_by_ad_library_id[ad.ad_library_id] = _public_result(ad, classification)
            scored = sorted(scored_by_ad_library_id.values(), key=_sort_key, reverse=True)
            job.progress_json = {
                **job.progress_json,
                "model_relevant": len(scored),
                "model_classification_failed": classification_failures,
                "selected_count": min(len(scored), job.target_count),
            }
            await session.commit()

            technical_rejection_summary = _technical_rejection_summary(
                technical_diagnostics_by_ad_library_id
            )
            model_exclusion_summary = _model_exclusion_summary(
                classification_diagnostics_by_ad_library_id
            )
            duplicate_count = raw_collected - len(candidates)
            round_summaries.append(
                {
                    "round": round_number,
                    "queries": queries,
                    "planned_query_count": len(planned_queries),
                    "skipped_repeated_query_count": len(planned_queries) - len(queries),
                    "round_raw_collected": round_raw_collected,
                    "round_new_candidates": len(candidates) - candidates_before_round,
                    "raw_collected": raw_collected,
                    "deduplicated": len(candidates),
                    "technical_qualified": technical_qualified,
                    "model_relevant": len(scored),
                    "selected_count": min(len(scored), job.target_count),
                }
            )
            gap_summary = {
                "raw_collected": raw_collected,
                "technical_qualified": technical_qualified,
                "model_relevant": len(scored),
                "target_count": job.target_count,
                "missing_count": max(job.target_count - len(scored), 0),
                "previous_queries": list(used_queries),
                "technical_rejection_summary": technical_rejection_summary,
                "model_exclusion_summary": model_exclusion_summary,
                "duplicate_count": duplicate_count,
            }

            if len(scored) >= job.target_count:
                selected = scored[: job.target_count]
                summary = _summary(
                    raw_collected,
                    len(candidates),
                    technical_qualified,
                    selected,
                    technical_diagnostics_by_ad_library_id,
                    classification_diagnostics_by_ad_library_id,
                    round_summaries,
                )
                await self.service.complete_job(
                    session, job, status="completed", ads=selected, summary=summary
                )
                return AdResearchRunResult("completed", selected, summary)
            if classification_failures:
                raise ProviderError(
                    "ad research model classification failed for "
                    f"{classification_failures} candidates"
                )
            if raw_collected >= MAX_RAW_CANDIDATES:
                break

        selected = scored[: job.target_count]
        summary = {
            **_summary(
                raw_collected,
                len(seen),
                technical_qualified,
                selected,
                technical_diagnostics_by_ad_library_id,
                classification_diagnostics_by_ad_library_id,
                round_summaries,
            ),
            "reason": "insufficient_qualified_ads",
            "max_rounds": MAX_ROUNDS,
            "max_raw_candidates": MAX_RAW_CANDIDATES,
        }
        await self.service.complete_job(
            session, job, status="insufficient", ads=selected, summary=summary
        )
        return AdResearchRunResult("insufficient", selected, summary)

    async def _classify_candidates(
        self, category: str, candidates: list[CollectorAd]
    ) -> list[dict[str, Any] | Exception]:
        semaphore = asyncio.Semaphore(max(int(self.settings.ad_research_model_concurrency), 1))

        async def classify_one(candidate: CollectorAd) -> dict[str, Any] | Exception:
            async with semaphore:
                try:
                    return await self.model.classify(category=category, candidate=candidate)
                except Exception as exc:  # Preserve failure state for the caller/job status.
                    return exc

        return await asyncio.gather(*(classify_one(candidate) for candidate in candidates))

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


def _keep(classification: dict[str, Any]) -> bool:
    return (
        classification.get("recommendation") == "keep"
        and bool(classification.get("category_match"))
        and not bool(classification.get("is_obviously_unrelated"))
        and float(classification.get("category_confidence") or 0) >= MIN_CATEGORY_CONFIDENCE
    )


def _public_result(ad: CollectorAd, classification: dict[str, Any]) -> dict[str, Any]:
    return {
        "ad_library_id": ad.ad_library_id,
        "advertiser_name": ad.advertiser_name,
        "ad_snapshot_url": ad.ad_snapshot_url,
        "text": "\n".join(ad.text_variants[:3]),
        "headline": ad.headline,
        "cta_text": ad.cta_text,
        "video_url": ad.video_url,
        "thumbnail_url": ad.thumbnail_url,
        "duration_seconds": ad.duration_seconds,
        "active_days": ad.days_running,
        "platforms": ad.platforms,
        "category_confidence": classification["category_confidence"],
        "creative_relevance_score": classification["creative_relevance_score"],
        "public_performance_signal_score": classification["public_performance_signal_score"],
        "real_money_signal_score": classification["real_money_signal_score"],
        "business_type": classification["business_type"],
        "evidence": {
            "text": classification["text_evidence"],
            "visual": classification["visual_evidence"],
            "public_signals": classification["public_signal_evidence"],
            "public_risk_signals": classification["public_risk_signals"],
        },
    }


def _sort_key(ad: dict[str, Any]) -> tuple[float, float, float, float]:
    return (
        float(ad["category_confidence"]),
        float(ad["creative_relevance_score"]),
        float(ad["public_performance_signal_score"]),
        float(ad.get("active_days") or 0),
    )


def _classification_diagnostic(ad: CollectorAd, classification: dict[str, Any]) -> dict[str, Any]:
    keep = _keep(classification)
    return {
        "ad_library_id": ad.ad_library_id,
        "category_match": bool(classification["category_match"]),
        "category_confidence": float(classification["category_confidence"]),
        "business_type": classification["business_type"],
        "creative_relevance_score": float(classification["creative_relevance_score"]),
        "public_performance_signal_score": float(classification["public_performance_signal_score"]),
        "real_money_signal_score": float(classification["real_money_signal_score"]),
        "is_obviously_unrelated": bool(classification["is_obviously_unrelated"]),
        "recommendation": classification["recommendation"],
        "decision": "keep" if keep else "exclude",
        "exclusion_reasons": [] if keep else _exclusion_reasons(classification),
        "text_evidence": classification["text_evidence"][:2],
        "visual_evidence": classification["visual_evidence"][:2],
        "public_signal_evidence": classification["public_signal_evidence"][:2],
        "public_risk_signals": classification["public_risk_signals"][:2],
    }


def _exclusion_reasons(classification: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    if classification.get("recommendation") != "keep":
        reasons.append("recommendation_not_keep")
    if not classification.get("category_match"):
        reasons.append("category_not_matched")
    if classification.get("is_obviously_unrelated"):
        reasons.append("obviously_unrelated")
    if float(classification.get("category_confidence") or 0) < MIN_CATEGORY_CONFIDENCE:
        reasons.append("category_confidence_below_threshold")
    return reasons


def _classification_diagnostics_summary(
    diagnostics_by_ad_library_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    candidates = list(diagnostics_by_ad_library_id.values())
    kept_count = sum(item["decision"] == "keep" for item in candidates)
    return {
        "classified_count": len(candidates),
        "kept_count": kept_count,
        "excluded_count": len(candidates) - kept_count,
        "minimum_category_confidence": MIN_CATEGORY_CONFIDENCE,
        "candidates": candidates,
    }


def _technical_rejection_summary(
    diagnostics_by_ad_library_id: dict[str, TechnicalQualification],
) -> dict[str, int]:
    summary: dict[str, int] = {}
    for diagnostic in diagnostics_by_ad_library_id.values():
        for reason in diagnostic.reasons:
            summary[reason] = summary.get(reason, 0) + 1
    return summary


def _model_exclusion_summary(
    diagnostics_by_ad_library_id: dict[str, dict[str, Any]],
) -> dict[str, int]:
    summary: dict[str, int] = {}
    for diagnostic in diagnostics_by_ad_library_id.values():
        for reason in diagnostic["exclusion_reasons"]:
            summary[reason] = summary.get(reason, 0) + 1
    return summary


def _summary(
    raw: int,
    deduplicated: int,
    qualified: int,
    selected: list[dict[str, Any]],
    technical_diagnostics_by_ad_library_id: dict[str, TechnicalQualification],
    classification_diagnostics_by_ad_library_id: dict[str, dict[str, Any]],
    round_summaries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "raw_collected": raw,
        "deduplicated": deduplicated,
        "technical_qualified": qualified,
        "model_relevant": len(selected),
        "selected_count": len(selected),
        "minimum_active_days": MIN_ACTIVE_DAYS,
        "maximum_video_seconds": MAX_VIDEO_SECONDS,
        "technical_rejection_summary": _technical_rejection_summary(
            technical_diagnostics_by_ad_library_id
        ),
        "model_exclusion_summary": _model_exclusion_summary(
            classification_diagnostics_by_ad_library_id
        ),
        "rounds": round_summaries,
        "classification_diagnostics": _classification_diagnostics_summary(
            classification_diagnostics_by_ad_library_id
        ),
        "performance_signal_notice": (
            "public_performance_signal_score is a public continuity proxy, not actual spend, "
            "CPC, CPA, ROAS, or conversion data."
        ),
    }
