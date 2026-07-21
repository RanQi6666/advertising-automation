from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.base import utcnow
from backend.app.db.models.ad_research_job import AdResearchJob
from backend.app.db.models.generation_task import GenerationTask
from backend.app.schemas.ad_research import CollectorAd
from backend.app.services.ad_research_collector import AdSourceAdapter, MetaAdsBridgeAdapter
from backend.app.services.ad_research_media import AdResearchMediaInspector
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

    async def run(self, session: AsyncSession, job: AdResearchJob) -> AdResearchRunResult:
        job.status = "processing"
        job.stage = "planning"
        job.started_at = job.started_at or utcnow()
        await session.commit()

        seen: dict[str, CollectorAd] = {}
        scored: list[dict[str, Any]] = []
        raw_collected = 0
        technical_qualified = 0
        gap_summary: dict[str, Any] = {}

        for round_number in range(1, MAX_ROUNDS + 1):
            job.current_round = round_number
            job.stage = "planning_queries"
            queries = await self.model.plan_queries(
                country=job.country,
                category=job.category,
                seed_keywords=list(job.seed_keywords_json or []),
                round_number=round_number,
                gap_summary=gap_summary,
            )
            if not queries:
                break
            job.stage = "collecting"
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
                for ad in ads:
                    seen.setdefault(ad.ad_library_id, ad)
            candidates = list(seen.values())
            job.stage = "technical_filtering"
            qualified = [ad for ad in candidates if await self.media.is_technically_qualified(ad)]
            technical_qualified = len(qualified)
            job.progress_json = {
                "raw_collected": raw_collected,
                "deduplicated": len(candidates),
                "technical_qualified": technical_qualified,
                "model_relevant": len(scored),
                "selected_count": min(len(scored), job.target_count),
            }
            await session.commit()

            job.stage = "model_classifying"
            classifications = await asyncio.gather(
                *(self.model.classify(category=job.category, candidate=ad) for ad in qualified),
                return_exceptions=True,
            )
            scored = []
            for ad, classification in zip(qualified, classifications, strict=True):
                if isinstance(classification, Exception):
                    continue
                if not _keep(classification):
                    continue
                scored.append(_public_result(ad, classification))
            scored.sort(key=_sort_key, reverse=True)
            job.progress_json = {
                **job.progress_json,
                "model_relevant": len(scored),
                "selected_count": min(len(scored), job.target_count),
            }
            await session.commit()
            if len(scored) >= job.target_count:
                selected = scored[: job.target_count]
                summary = _summary(raw_collected, len(candidates), technical_qualified, selected)
                await self.service.complete_job(
                    session, job, status="completed", ads=selected, summary=summary
                )
                return AdResearchRunResult("completed", selected, summary)
            gap_summary = {
                "raw_collected": raw_collected,
                "technical_qualified": technical_qualified,
                "model_relevant": len(scored),
                "target_count": job.target_count,
                "missing_count": max(job.target_count - len(scored), 0),
            }
            if raw_collected >= MAX_RAW_CANDIDATES:
                break

        selected = scored[: job.target_count]
        summary = {
            **_summary(raw_collected, len(seen), technical_qualified, selected),
            "reason": "insufficient_qualified_ads",
            "max_rounds": MAX_ROUNDS,
            "max_raw_candidates": MAX_RAW_CANDIDATES,
        }
        await self.service.complete_job(
            session, job, status="insufficient", ads=selected, summary=summary
        )
        return AdResearchRunResult("insufficient", selected, summary)

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


def _summary(
    raw: int, deduplicated: int, qualified: int, selected: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "raw_collected": raw,
        "deduplicated": deduplicated,
        "technical_qualified": qualified,
        "model_relevant": len(selected),
        "selected_count": len(selected),
        "performance_signal_notice": (
            "public_performance_signal_score is a public continuity proxy, not actual spend, "
            "CPC, CPA, ROAS, or conversion data."
        ),
    }
