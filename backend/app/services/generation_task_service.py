import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.core.errors import AppError
from backend.app.db.base import utcnow
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.session import AsyncSessionLocal
from backend.app.schemas.copywriting import CopyDraftRead, CopyGenerateRequest, CopyReviseRequest
from backend.app.schemas.topic import TopicGenerateRequest, TopicRead
from backend.app.services.copywriting_service import CopywritingService
from backend.app.services.generation_attempt_service import classify_generation_error
from backend.app.services.topic_service import TopicService
from backend.app.services.utils import get_required

TEXT_QUEUE_NAME = "text_queue"
TEXT_TASK_TYPES = {"topic_generate", "copy_generate", "copy_revise"}
RETRYABLE_TASK_ERROR_CODES = {
    "provider_timeout",
    "provider_429",
    "external_url_unreachable",
    "unknown_provider_error",
}

T = TypeVar("T")

_text_queue_semaphore: asyncio.Semaphore | None = None
_text_queue_limit: int | None = None


class GenerationTaskService:
    async def create_task(
        self,
        session: AsyncSession,
        *,
        queue_name: str,
        task_type: str,
        business_type: str,
        business_id: str,
        payload: dict[str, Any],
        campaign_id: str | None = None,
        priority: int = 0,
        max_attempts: int = 2,
        metadata: dict[str, Any] | None = None,
    ) -> GenerationTask:
        task = GenerationTask(
            queue_name=queue_name,
            task_type=task_type,
            business_type=business_type,
            business_id=business_id,
            campaign_id=campaign_id,
            status="queued",
            priority=priority,
            payload_json=payload,
            retryable=False,
            attempt_count=0,
            max_attempts=max(max_attempts, 1),
            queued_at=utcnow(),
            metadata_json=metadata or {},
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task

    async def get_task(self, session: AsyncSession, task_id: str) -> GenerationTask:
        return await get_required(session, GenerationTask, task_id)  # type: ignore[return-value]

    async def retry_task(self, session: AsyncSession, task_id: str) -> GenerationTask:
        task = await self.get_task(session, task_id)
        if task.task_type == "image_brief_generate":
            raise AppError("Image brief queue tasks are retried by regenerating the image request.")
        if task.status not in {"failed", "queued"}:
            raise AppError("Only failed or queued generation tasks can be retried.")
        if task.status == "failed" and not task.retryable:
            raise AppError("This generation task is not retryable.")
        task.status = "queued"
        task.result_json = None
        task.error_code = None
        task.error_message = None
        task.retryable = False
        task.queued_at = utcnow()
        task.started_at = None
        task.finished_at = None
        task.duration_ms = None
        await session.commit()
        await session.refresh(task)
        return task

    async def process_task(self, task_id: str) -> None:
        async with _text_queue_capacity():
            async with AsyncSessionLocal() as session:
                task = await self.get_task(session, task_id)
                if task.status != "queued":
                    return
                await self._mark_running(session, task)
                try:
                    result = await self._run_task(session, task)
                except Exception as exc:
                    await self._mark_failed(session, task, exc)
                    return
                await self._mark_succeeded(session, task, result)

    async def run_inline(
        self,
        *,
        task_type: str,
        business_type: str,
        business_id: str,
        payload: dict[str, Any],
        operation: Callable[[], Awaitable[T]],
        result_serializer: Callable[[T], dict[str, Any]],
        campaign_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        async with AsyncSessionLocal() as session:
            task = await self.create_task(
                session,
                queue_name=TEXT_QUEUE_NAME,
                task_type=task_type,
                business_type=business_type,
                business_id=business_id,
                campaign_id=campaign_id,
                payload=payload,
                metadata=metadata,
                max_attempts=1,
            )

        async with _text_queue_capacity():
            async with AsyncSessionLocal() as session:
                task = await self.get_task(session, task.id)
                await self._mark_running(session, task)
            try:
                result = await operation()
            except Exception as exc:
                async with AsyncSessionLocal() as session:
                    task = await self.get_task(session, task.id)
                    await self._mark_failed(session, task, exc)
                raise
            async with AsyncSessionLocal() as session:
                task = await self.get_task(session, task.id)
                await self._mark_succeeded(session, task, result_serializer(result))
            return result

    async def run_in_text_queue(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with _text_queue_capacity():
            return await operation()

    async def _run_task(self, session: AsyncSession, task: GenerationTask) -> dict[str, Any]:
        if task.queue_name != TEXT_QUEUE_NAME or task.task_type not in TEXT_TASK_TYPES:
            raise AppError(f"Unsupported generation task: {task.queue_name}/{task.task_type}")

        payload = task.payload_json or {}
        if task.task_type == "topic_generate":
            topics = await TopicService().generate_topics(
                session,
                TopicGenerateRequest.model_validate(payload),
            )
            return {
                "topics": [
                    TopicRead.model_validate(topic).model_dump(mode="json") for topic in topics
                ],
                "generated_count": len(topics),
            }

        if task.task_type == "copy_generate":
            draft = await CopywritingService().generate_copy(
                session,
                CopyGenerateRequest.model_validate(payload),
            )
            return {"draft": CopyDraftRead.model_validate(draft).model_dump(mode="json")}

        if task.task_type == "copy_revise":
            draft_id = str(payload.get("draft_id") or "")
            if not draft_id:
                raise AppError("draft_id is required for copy revision tasks.")
            request_payload = payload.get("request")
            if not isinstance(request_payload, dict):
                raise AppError("request is required for copy revision tasks.")
            draft = await CopywritingService().revise_copy(
                session,
                draft_id,
                CopyReviseRequest.model_validate(request_payload),
            )
            return {"draft": CopyDraftRead.model_validate(draft).model_dump(mode="json")}

        raise AppError(f"Unsupported text task type: {task.task_type}")

    async def _mark_running(self, session: AsyncSession, task: GenerationTask) -> None:
        task.status = "running"
        task.attempt_count += 1
        task.started_at = utcnow()
        task.finished_at = None
        task.duration_ms = None
        task.error_code = None
        task.error_message = None
        task.retryable = False
        await session.commit()
        await session.refresh(task)

    async def _mark_succeeded(
        self,
        session: AsyncSession,
        task: GenerationTask,
        result: dict[str, Any],
    ) -> None:
        task.status = "succeeded"
        task.result_json = result
        task.error_code = None
        task.error_message = None
        task.retryable = False
        task.finished_at = utcnow()
        task.duration_ms = _duration_ms(task.started_at, task.finished_at)
        await session.commit()
        await session.refresh(task)

    async def _mark_failed(
        self,
        session: AsyncSession,
        task: GenerationTask,
        exc: Exception,
    ) -> None:
        message = str(exc) or exc.__class__.__name__
        error_code = classify_generation_error(message)
        task.status = "failed"
        task.error_code = error_code
        task.error_message = message
        task.retryable = (
            task.attempt_count < task.max_attempts and error_code in RETRYABLE_TASK_ERROR_CODES
        )
        task.finished_at = utcnow()
        task.duration_ms = _duration_ms(task.started_at, task.finished_at)
        await session.commit()
        await session.refresh(task)


def _text_queue_capacity() -> asyncio.Semaphore:
    global _text_queue_limit, _text_queue_semaphore
    limit = get_settings().text_queue_concurrency
    if _text_queue_semaphore is None or _text_queue_limit != limit:
        _text_queue_semaphore = asyncio.Semaphore(limit)
        _text_queue_limit = limit
    return _text_queue_semaphore


def _duration_ms(started_at: datetime | None, finished_at: datetime | None) -> int | None:
    if started_at is None or finished_at is None:
        return None
    try:
        return max(int((finished_at - started_at).total_seconds() * 1000), 0)
    except TypeError:
        duration = finished_at.replace(tzinfo=None) - started_at.replace(tzinfo=None)
        return max(
            int(duration.total_seconds() * 1000),
            0,
        )
