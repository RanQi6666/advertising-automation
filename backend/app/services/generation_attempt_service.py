import asyncio
from collections.abc import AsyncIterator, Iterable
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.base import utcnow
from backend.app.db.models.generation_attempt import GenerationAttempt
from backend.app.services.utils import get_required

RETRYABLE_ERROR_CODES = {
    "provider_timeout",
    "provider_429",
    "stream_interrupted",
    "external_url_unreachable",
    "storage_missing",
    "unknown_provider_error",
}


class GenerationAttemptService:
    async def create_attempt(
        self,
        session: AsyncSession,
        *,
        business_type: str,
        business_id: str,
        stage: str,
        total_count: int = 1,
        campaign_id: str | None = None,
        job_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationAttempt:
        now = utcnow()
        attempt = GenerationAttempt(
            business_type=business_type,
            business_id=business_id,
            campaign_id=campaign_id,
            job_id=job_id,
            stage=stage,
            status="running",
            total_count=max(total_count, 0),
            success_count=0,
            failed_count=0,
            provider=provider,
            model=model,
            retryable=False,
            started_at=now,
            metadata_json=metadata or {},
        )
        session.add(attempt)
        await session.commit()
        await session.refresh(attempt)
        return attempt

    async def get_attempt(self, session: AsyncSession, attempt_id: str) -> GenerationAttempt:
        return await get_required(session, GenerationAttempt, attempt_id)  # type: ignore[return-value]

    async def finish_attempt(
        self,
        session: AsyncSession,
        attempt: GenerationAttempt,
        *,
        success_count: int,
        failed_count: int,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool | None = None,
        metadata_update: dict[str, Any] | None = None,
    ) -> GenerationAttempt:
        success_count = max(success_count, 0)
        failed_count = max(failed_count, 0)
        if attempt.total_count:
            success_count = min(success_count, attempt.total_count)
            failed_count = min(
                max(failed_count, attempt.total_count - success_count),
                attempt.total_count,
            )
        if failed_count and error_code is None:
            error_code = classify_generation_error(error_message)

        attempt.success_count = success_count
        attempt.failed_count = failed_count
        attempt.status = _attempt_status(
            total_count=attempt.total_count,
            success_count=success_count,
            failed_count=failed_count,
        )
        attempt.error_code = error_code
        attempt.error_message = error_message
        attempt.retryable = (
            retryable if retryable is not None else error_code in RETRYABLE_ERROR_CODES
        )
        if metadata_update:
            attempt.metadata_json = {**(attempt.metadata_json or {}), **metadata_update}
        attempt.finished_at = utcnow()
        attempt.duration_ms = _duration_ms(attempt.started_at, attempt.finished_at)
        await session.commit()
        await session.refresh(attempt)
        return attempt

    async def track_stream(
        self,
        *,
        session: AsyncSession,
        attempt: GenerationAttempt,
        events: AsyncIterator[dict],
        success_event_types: Iterable[str],
        store_last_success_event: bool = False,
    ) -> AsyncIterator[dict]:
        success_types = set(success_event_types)
        success_count = 0
        error_messages: list[str] = []
        last_success_event: dict | None = None
        start_seen = False

        try:
            async for event in events:
                event_type = str(event.get("type") or "")
                if event_type == "start":
                    start_seen = True
                    yield _event_with_attempt(event, attempt)
                    continue
                if event_type in success_types:
                    success_count += 1
                    last_success_event = event
                if event_type == "error":
                    message = str(event.get("message") or "")
                    if message:
                        error_messages.append(message)
                yield event

            error_message = error_messages[-1] if error_messages else None
            failed_count = _failed_count(attempt.total_count, success_count, error_messages)
            await self.finish_attempt(
                session,
                attempt,
                success_count=success_count,
                failed_count=failed_count,
                error_code=classify_generation_error(error_message) if error_message else None,
                error_message=error_message,
                metadata_update=(
                    {"last_success_event": last_success_event}
                    if store_last_success_event and last_success_event
                    else None
                ),
            )
        except asyncio.CancelledError:
            await self.finish_attempt(
                session,
                attempt,
                success_count=success_count,
                failed_count=max(attempt.total_count - success_count, 1),
                error_code="stream_interrupted",
                error_message="Client disconnected before the generation stream completed.",
                retryable=True,
            )
            raise
        except Exception as exc:
            message = str(exc) or exc.__class__.__name__
            await self.finish_attempt(
                session,
                attempt,
                success_count=success_count,
                failed_count=max(attempt.total_count - success_count, 1),
                error_code=classify_generation_error(message),
                error_message=message,
            )
            if not start_seen:
                yield _event_with_attempt({"type": "start"}, attempt)
            yield {"type": "error", "message": message}
            yield {"type": "done", "generated": success_count}


def classify_generation_error(message: str | None) -> str:
    if not message:
        return "provider_no_output"
    normalized = message.lower()
    if "timeout" in normalized or "timed out" in normalized:
        return "provider_timeout"
    if "429" in normalized or "rate limit" in normalized or "too many requests" in normalized:
        return "provider_429"
    if "400" in normalized or "badrequest" in normalized or "bad request" in normalized:
        return "provider_400"
    if "url" in normalized and (
        "unreachable" in normalized or "download" in normalized or "ssl" in normalized
    ):
        return "external_url_unreachable"
    return "unknown_provider_error"


def _event_with_attempt(event: dict, attempt: GenerationAttempt) -> dict:
    return {
        **event,
        "attempt_id": attempt.id,
        "total_count": attempt.total_count,
    }


def _failed_count(total_count: int, success_count: int, error_messages: list[str]) -> int:
    if total_count <= 0:
        return len(error_messages)
    return max(total_count - success_count, len(error_messages))


def _attempt_status(*, total_count: int, success_count: int, failed_count: int) -> str:
    if total_count and success_count >= total_count and failed_count == 0:
        return "succeeded"
    if success_count > 0:
        return "partial_succeeded"
    if failed_count > 0 or total_count > 0:
        return "failed"
    return "succeeded"


def _duration_ms(started_at: datetime, finished_at: datetime | None) -> int | None:
    if finished_at is None:
        return None
    try:
        return max(int((finished_at - started_at).total_seconds() * 1000), 0)
    except TypeError:
        return max(
            int(
                (
                    finished_at.replace(tzinfo=None) - started_at.replace(tzinfo=None)
                ).total_seconds()
                * 1000
            ),
            0,
        )
