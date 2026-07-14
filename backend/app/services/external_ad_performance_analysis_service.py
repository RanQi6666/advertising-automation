from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppError, NotFoundError
from backend.app.db.base import utcnow
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.db.models.generation_task import GenerationTask
from backend.app.integrations.llm.factory import get_llm_provider
from backend.app.schemas.external_ad_performance_analysis import (
    ExternalAdPerformanceAnalysisCreate,
    ad_analysis_payload_hash,
    canonicalize_ad_analysis_payload,
)
from backend.app.services.ad_analysis_media_service import (
    AdAnalysisMediaService,
    public_media_summary,
)
from backend.app.services.ad_analysis_research_service import AdAnalysisResearchService
from backend.app.services.facebook_ad_analysis_assembler import (
    RULE_RESULT_SCHEMA_VERSION,
    assemble_facebook_ad_analysis,
)
from backend.app.services.facebook_ad_metrics import build_facebook_metric_analysis
from backend.app.services.generation_task_service import AD_ANALYSIS_QUEUE_NAME

AD_ANALYSIS_TASK_TYPE = "ad_performance_analysis"
AD_ANALYSIS_BUSINESS_TYPE = "ad_performance_analysis"
AD_ANALYSIS_SCOPE = "facebook_ad_performance"

logger = logging.getLogger(__name__)


class AdAnalysisIdempotencyConflict(AppError):
    """Raised when the same external_request_id is reused for different content."""


@dataclass(frozen=True)
class AnalysisJobCreateResult:
    analysis: AdPerformanceAnalysis
    task: GenerationTask | None
    idempotent_replay: bool = False


class ExternalAdPerformanceAnalysisService:
    async def create_analysis_job(
        self,
        session: AsyncSession,
        payload: ExternalAdPerformanceAnalysisCreate,
    ) -> AnalysisJobCreateResult:
        normalized_payload = canonicalize_ad_analysis_payload(payload)
        payload_hash = ad_analysis_payload_hash(payload)
        existing = await self._get_by_external_request_id(
            session,
            payload.external_request_id,
        )
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise AdAnalysisIdempotencyConflict(
                    "external_request_id already exists with a different normalized payload."
                )
            return AnalysisJobCreateResult(
                analysis=existing,
                task=await self._dispatch_retry_task(session, existing),
                idempotent_replay=True,
            )

        analysis_record_id = str(uuid4())
        task_id = str(uuid4())
        analysis_id = _new_analysis_id()
        request_payload = payload.model_dump(mode="python", exclude_none=True)
        now = utcnow()
        analysis = AdPerformanceAnalysis(
            id=analysis_record_id,
            analysis_id=analysis_id,
            external_request_id=payload.external_request_id,
            payload_hash=payload_hash,
            request_payload=request_payload,
            normalized_payload=normalized_payload,
            external_user_id=payload.external_user_id,
            source_type=payload.source_type or "external",
            status="queued",
            stage="queued",
            progress=0,
            analysis_scope=AD_ANALYSIS_SCOPE,
            result_schema_version=RULE_RESULT_SCHEMA_VERSION,
            campaign_external_id=_text(
                _dict(payload.campaign).get("id") or _dict(payload.campaign).get("external_id")
            ),
            campaign_name=_text(_dict(payload.campaign).get("name")),
            adset_external_id=_text(
                _dict(payload.adset).get("id") or _dict(payload.adset).get("external_id")
            ),
            adset_name=_text(_dict(payload.adset).get("name")),
            creative_external_id=_text(
                _dict(payload.creative).get("id") or _dict(payload.creative).get("external_id")
            ),
            creative_name=_text(_dict(payload.creative).get("name")),
            date_start=payload.date_start,
            date_stop=payload.date_stop,
            metrics={},
            analysis_result={},
            media_summary={},
            research_summary={},
            attempt_count=0,
            max_attempts=2,
        )
        task = GenerationTask(
            id=task_id,
            queue_name=AD_ANALYSIS_QUEUE_NAME,
            task_type=AD_ANALYSIS_TASK_TYPE,
            business_type=AD_ANALYSIS_BUSINESS_TYPE,
            business_id=analysis_id,
            status="queued",
            priority=0,
            payload_json={
                "analysis_record_id": analysis_record_id,
                "analysis_id": analysis_id,
            },
            result_json=None,
            retryable=False,
            attempt_count=0,
            max_attempts=2,
            queued_at=now,
            metadata_json={
                "source": "external_ad_performance_analysis",
                "external_request_id": payload.external_request_id,
                "payload_hash": payload_hash,
            },
        )
        analysis.generation_task_id = task_id
        session.add(analysis)
        session.add(task)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            concurrent = await self._get_by_external_request_id(
                session,
                payload.external_request_id,
            )
            if concurrent is not None and concurrent.payload_hash == payload_hash:
                return AnalysisJobCreateResult(
                    analysis=concurrent,
                    task=await self._dispatch_retry_task(session, concurrent),
                    idempotent_replay=True,
                )
            raise AdAnalysisIdempotencyConflict(
                "external_request_id already exists with a different normalized payload."
            ) from exc
        await session.refresh(analysis)
        await session.refresh(task)
        return AnalysisJobCreateResult(analysis=analysis, task=task)

    async def record_dispatch_success(
        self,
        session: AsyncSession,
        analysis: AdPerformanceAnalysis,
    ) -> None:
        analysis.dispatch_attempts = int(analysis.dispatch_attempts or 0) + 1
        analysis.last_dispatched_at = utcnow()
        analysis.dispatch_error = None
        analysis.dispatch_claimed_by = None
        analysis.dispatch_claimed_at = None
        await session.commit()

    async def record_dispatch_failure(
        self,
        session: AsyncSession,
        analysis: AdPerformanceAnalysis,
        exc: Exception,
    ) -> None:
        analysis.dispatch_attempts = int(analysis.dispatch_attempts or 0) + 1
        analysis.dispatch_error = (str(exc).strip() or exc.__class__.__name__)[:2000]
        analysis.dispatch_claimed_by = None
        analysis.dispatch_claimed_at = None
        await session.commit()

    async def get_analysis_job(
        self,
        session: AsyncSession,
        analysis_id: str,
    ) -> AdPerformanceAnalysis:
        result = await session.execute(
            select(AdPerformanceAnalysis).where(
                AdPerformanceAnalysis.analysis_id == analysis_id,
                AdPerformanceAnalysis.analysis_scope == AD_ANALYSIS_SCOPE,
            )
        )
        analysis = result.scalar_one_or_none()
        if analysis is None:
            raise NotFoundError("analysis_id was not found.")
        return analysis

    async def _dispatch_retry_task(
        self,
        session: AsyncSession,
        analysis: AdPerformanceAnalysis,
    ) -> GenerationTask | None:
        if analysis.status != "queued" or not analysis.generation_task_id:
            return None
        if analysis.last_dispatched_at is not None and not analysis.dispatch_error:
            return None
        task = await session.get(GenerationTask, analysis.generation_task_id)
        if task is None or task.status != "queued":
            return None
        return task

    async def execute_task(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> dict[str, Any]:
        analysis = await self._get_analysis_for_task(session, task)
        if analysis.status == "succeeded" and analysis.analysis_result:
            return {
                "analysis_id": analysis.analysis_id,
                "status": analysis.status,
                "result_schema_version": analysis.result_schema_version,
            }

        try:
            await self._mark_processing(session, analysis, task, stage="rules", progress=10)
            payload = _dict(analysis.normalized_payload) or _dict(analysis.request_payload)
            rule_analysis = build_facebook_metric_analysis(payload)
            await self._update_stage(session, analysis, "media_processing", 30)
            media_result = await AdAnalysisMediaService().process_media(
                payload,
                analysis_id=str(analysis.analysis_id or analysis.id),
            )
            media_summary = media_result.summary
            analysis.media_summary = public_media_summary(media_summary)
            await session.commit()

            await self._update_stage(session, analysis, "public_research", 55)
            research_summary = (
                await AdAnalysisResearchService().research_and_persist(
                    session,
                    analysis,
                    payload,
                    media_summary=media_summary,
                )
            ).summary
            analysis.research_summary = research_summary
            await session.commit()

            await self._update_stage(session, analysis, "llm_analysis", 75)
            llm_contribution = await self._safe_llm_contribution(
                payload=payload,
                rule_analysis=rule_analysis,
                media_summary=media_summary,
                research_summary=research_summary,
            )
            if llm_contribution.get("_warning"):
                _append_warning(media_summary, str(llm_contribution.pop("_warning")))
                analysis.media_summary = public_media_summary(media_summary)
                await session.commit()

            result = assemble_facebook_ad_analysis(
                request_payload=payload,
                rule_analysis=rule_analysis,
                media_analysis=media_summary,
                public_research=research_summary,
                llm_contribution=llm_contribution,
            )
            analysis.metrics = rule_analysis.get("metrics") or {}
            analysis.analysis_result = result
            analysis.media_summary = public_media_summary(media_summary)
            analysis.result_schema_version = RULE_RESULT_SCHEMA_VERSION
            analysis.status = "succeeded"
            analysis.stage = "completed"
            analysis.progress = 100
            analysis.error_code = None
            analysis.error_message = None
            analysis.error_retryable = False
            analysis.completed_at = utcnow()
            await session.commit()
            await session.refresh(analysis)
            await AdAnalysisMediaService().cleanup_analysis_media(
                str(analysis.analysis_id or analysis.id)
            )
            return {
                "analysis_id": analysis.analysis_id,
                "status": analysis.status,
                "result_schema_version": analysis.result_schema_version,
            }
        except Exception as exc:
            logger.exception("External ad-performance analysis job failed.")
            await session.rollback()
            analysis = await session.get(AdPerformanceAnalysis, analysis.id)
            if analysis is not None:
                analysis.media_summary = public_media_summary(analysis.media_summary)
                analysis.status = "failed"
                analysis.stage = "failed"
                analysis.progress = analysis.progress or 0
                analysis.error_code = _error_code(exc)
                analysis.error_message = str(exc) or exc.__class__.__name__
                analysis.error_retryable = bool(task.attempt_count < task.max_attempts)
                analysis.completed_at = utcnow()
                await session.commit()
            await AdAnalysisMediaService().cleanup_analysis_media(str(task.business_id or ""))
            raise

    async def _get_by_external_request_id(
        self,
        session: AsyncSession,
        external_request_id: str,
    ) -> AdPerformanceAnalysis | None:
        result = await session.execute(
            select(AdPerformanceAnalysis).where(
                AdPerformanceAnalysis.external_request_id == external_request_id,
                AdPerformanceAnalysis.analysis_scope == AD_ANALYSIS_SCOPE,
            )
        )
        return result.scalar_one_or_none()

    async def _get_analysis_for_task(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> AdPerformanceAnalysis:
        payload = task.payload_json or {}
        analysis_record_id = str(payload.get("analysis_record_id") or "")
        analysis_id = str(payload.get("analysis_id") or task.business_id or "")
        analysis = None
        if analysis_record_id:
            analysis = await session.get(AdPerformanceAnalysis, analysis_record_id)
        if analysis is None and analysis_id:
            analysis = await self.get_analysis_job(session, analysis_id)
        if analysis is None:
            raise NotFoundError("analysis job record was not found.")
        return analysis

    async def _mark_processing(
        self,
        session: AsyncSession,
        analysis: AdPerformanceAnalysis,
        task: GenerationTask,
        *,
        stage: str,
        progress: int,
    ) -> None:
        analysis.status = "processing"
        analysis.stage = stage
        analysis.progress = progress
        analysis.started_at = analysis.started_at or utcnow()
        analysis.attempt_count = max(int(analysis.attempt_count or 0), int(task.attempt_count or 0))
        analysis.error_code = None
        analysis.error_message = None
        analysis.error_retryable = False
        await session.commit()
        await session.refresh(analysis)

    async def _update_stage(
        self,
        session: AsyncSession,
        analysis: AdPerformanceAnalysis,
        stage: str,
        progress: int,
    ) -> None:
        analysis.status = "processing"
        analysis.stage = stage
        analysis.progress = max(min(int(progress), 99), 0)
        await session.commit()
        await session.refresh(analysis)

    async def _safe_llm_contribution(
        self,
        *,
        payload: dict[str, Any],
        rule_analysis: dict[str, Any],
        media_summary: dict[str, Any],
        research_summary: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            provider = get_llm_provider()
            return await provider.analyze_ad_performance(
                {
                    **payload,
                    "metrics": rule_analysis.get("metrics") or {},
                    "rule_analysis": rule_analysis,
                    "media_summary": media_summary,
                    "public_research": research_summary,
                    "result_contract": {
                        "schema_version": RULE_RESULT_SCHEMA_VERSION,
                        "operator_sections": [
                            "summary",
                            "overall_decision",
                            "targeting_analysis",
                            "adjustment_plans",
                            "copywriting_analysis",
                            "media_analysis",
                            "market_intelligence",
                            "data_gaps",
                        ],
                        "rule_owned_facts": [
                            "metrics",
                            "primary_bottleneck",
                            "overall_action",
                            "overall_priority",
                            "data_quality",
                        ],
                    },
                }
            )
        except Exception as exc:  # noqa: BLE001 - LLM failure degrades to rules-based result.
            return {
                "_warning": (
                    "LLM contribution unavailable; deterministic Meta metrics and "
                    "fallback recommendations were used: "
                    f"{str(exc) or exc.__class__.__name__}"
                )
            }


def _new_analysis_id() -> str:
    return f"ana_{uuid4().hex}"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _append_warning(summary: dict[str, Any], warning: str) -> None:
    warnings = summary.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    if warning not in warnings:
        warnings.append(warning)
    summary["warnings"] = warnings


def _error_code(exc: Exception) -> str:
    if isinstance(exc, NotFoundError):
        return "analysis_not_found"
    if isinstance(exc, AppError):
        return "ad_analysis_error"
    return "ad_analysis_unexpected_error"
