from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppError, NotFoundError
from backend.app.db.base import utcnow
from backend.app.db.models.ad_research_job import AdResearchJob
from backend.app.db.models.generation_task import GenerationTask
from backend.app.schemas.ad_research import AdResearchCreateRequest, AdResearchPollResponse

AD_RESEARCH_QUEUE_NAME = "ad_research_queue"
AD_RESEARCH_TASK_TYPE = "ad_research"
AD_RESEARCH_BUSINESS_TYPE = "ad_research"
RESULT_RETENTION_HOURS = 24


class AdResearchIdempotencyConflict(AppError):
    pass


class AdResearchResultExpired(AppError):
    pass


@dataclass(frozen=True)
class AdResearchCreateResult:
    job: AdResearchJob
    task: GenerationTask | None
    idempotent_replay: bool = False


def canonicalize_request(
    country: str, category: str, keywords: list[str], target_count: int
) -> dict[str, Any]:
    normalized_keywords = sorted(
        {str(keyword).strip().casefold() for keyword in keywords if str(keyword).strip()}
    )
    return {
        "country": country.strip().upper(),
        "category": category.strip().casefold(),
        "keywords": normalized_keywords,
        "target_count": int(target_count),
    }


def request_fingerprint(country: str, category: str, keywords: list[str], target_count: int) -> str:
    value = canonicalize_request(country, category, keywords, target_count)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class AdResearchService:
    async def create_job(
        self, session: AsyncSession, payload: AdResearchCreateRequest
    ) -> AdResearchCreateResult:
        fingerprint = request_fingerprint(
            payload.country, payload.category, payload.keywords, payload.target_count
        )
        existing = await session.scalar(
            select(AdResearchJob).where(AdResearchJob.external_user_id == payload.external_user_id)
        )
        if existing is not None:
            if existing.is_result_expired:
                await self._expire_job(session, existing)
            else:
                if existing.request_fingerprint != fingerprint:
                    raise AdResearchIdempotencyConflict(
                        "external_user_id already exists with a different normalized payload."
                    )
                return AdResearchCreateResult(job=existing, task=None, idempotent_replay=True)

        now = utcnow()
        job_id = f"adr_{uuid4().hex}"
        task_id = str(uuid4())
        job = AdResearchJob(
            id=job_id,
            external_user_id=payload.external_user_id,
            request_fingerprint=fingerprint,
            country=payload.country.upper(),
            category=payload.category.strip(),
            seed_keywords_json=list(payload.keywords),
            target_count=payload.target_count,
            status="queued",
            stage="queued",
            current_round=0,
            progress_json=_initial_progress(),
            summary_json={},
            result_json=None,
            generation_task_id=task_id,
        )
        task = GenerationTask(
            id=task_id,
            queue_name=AD_RESEARCH_QUEUE_NAME,
            task_type=AD_RESEARCH_TASK_TYPE,
            business_type=AD_RESEARCH_BUSINESS_TYPE,
            business_id=job_id,
            status="queued",
            priority=0,
            payload_json={"job_id": job_id},
            result_json=None,
            retryable=False,
            attempt_count=0,
            max_attempts=1,
            queued_at=now,
            metadata_json={
                "source": "external_ad_research",
                "external_user_id": payload.external_user_id,
                "request_fingerprint": fingerprint,
            },
        )
        session.add_all([job, task])
        await session.commit()
        await session.refresh(job)
        await session.refresh(task)
        return AdResearchCreateResult(job=job, task=task)

    async def get_job(self, session: AsyncSession, job_id: str) -> AdResearchJob:
        job = await session.get(AdResearchJob, job_id)
        if job is None:
            raise NotFoundError("ad research task was not found.")
        if job.is_result_expired:
            await self._expire_job(session, job)
            raise AdResearchResultExpired("ad research result has expired after 24 hours.")
        return job

    def poll_response(self, job: AdResearchJob) -> AdResearchPollResponse:
        final = job.status in {"completed", "insufficient"}
        error = None
        if job.status == "failed":
            error = {
                "code": job.error_code or "ad_research_failed",
                "message": job.error_message_summary or "ad research task failed",
            }
        result = job.result_json or {}
        return AdResearchPollResponse(
            task_id=job.id,
            external_user_id=job.external_user_id,
            status=job.status,
            stage=job.stage,
            round=job.current_round,
            progress=job.progress_json or {},
            poll_after_seconds=None if final or job.status == "failed" else 3,
            research_summary=job.summary_json or {},
            ads=list(result.get("ads") or []) if final else None,
            result_expires_at=job.result_expires_at if final else None,
            error=error,
        )

    async def mark_dispatch_failure(
        self, session: AsyncSession, job: AdResearchJob, error: Exception
    ) -> None:
        job.status = "failed"
        job.stage = "dispatch_failed"
        job.error_code = "queue_dispatch_failed"
        job.error_message_summary = _safe_error(error)
        await session.commit()

    async def complete_job(
        self,
        session: AsyncSession,
        job: AdResearchJob,
        *,
        status: str,
        ads: list[dict[str, Any]],
        summary: dict[str, Any],
    ) -> None:
        now = utcnow()
        job.status = status
        job.stage = "completed" if status == "completed" else "insufficient"
        job.result_json = {"ads": ads}
        job.summary_json = summary
        job.progress_json = {**(job.progress_json or {}), "selected_count": len(ads)}
        job.completed_at = now
        job.result_expires_at = now + timedelta(hours=RESULT_RETENTION_HOURS)
        job.error_code = None
        job.error_message_summary = None
        await session.commit()

    async def fail_job(
        self, session: AsyncSession, job: AdResearchJob, error: Exception
    ) -> None:
        job.status = "failed"
        job.stage = "failed"
        job.error_code = "ad_research_unexpected_error"
        job.error_message_summary = _safe_error(error)
        job.completed_at = utcnow()
        await session.commit()

    async def cleanup_expired_results(self, session: AsyncSession) -> int:
        rows = list(
            (
                await session.execute(
                    select(AdResearchJob).where(
                        AdResearchJob.result_expires_at.is_not(None),
                        AdResearchJob.result_expires_at <= utcnow(),
                        AdResearchJob.status.in_(("completed", "insufficient")),
                    )
                )
            ).scalars()
        )
        for job in rows:
            await self._expire_job(session, job, commit=False)
        if rows:
            await session.commit()
        return len(rows)

    async def _expire_job(
        self, session: AsyncSession, job: AdResearchJob, *, commit: bool = True
    ) -> None:
        job.status = "expired"
        job.stage = "expired"
        job.result_json = None
        job.result_expires_at = None
        job.expired_at = utcnow()
        job.external_user_id = None
        job.summary_json = {"status": "expired", "retention_hours": RESULT_RETENTION_HOURS}
        if commit:
            await session.commit()


def _initial_progress() -> dict[str, int]:
    return {
        "raw_collected": 0,
        "deduplicated": 0,
        "technical_qualified": 0,
        "model_relevant": 0,
        "selected_count": 0,
    }


def _safe_error(error: Exception) -> str:
    value = str(error).replace("\n", " ").strip()
    return (value or error.__class__.__name__)[:300]
