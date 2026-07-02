import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import Lock
from typing import Any, TypeVar

from sqlalchemy import func, select
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
IMAGE_QUEUE_NAME = "image_queue"
VIDEO_QUEUE_NAME = "video_queue"
CALLBACK_QUEUE_NAME = "callback_queue"
TEXT_TASK_TYPES = {"topic_generate", "copy_generate", "copy_revise"}
IMAGE_TASK_TYPES = {"image_generate"}
VIDEO_TASK_TYPES = {"video_generate"}
CALLBACK_TASK_TYPES = {"ad_generation_callback"}
ACTIVE_TASK_STATUSES = {"queued", "running"}
IDEMPOTENCY_KEY_METADATA_FIELD = "idempotency_key"
RETRYABLE_TASK_ERROR_CODES = {
    "provider_timeout",
    "provider_429",
    "task_interrupted",
    "task_stale",
    "external_url_unreachable",
    "unknown_provider_error",
}

T = TypeVar("T")
logger = logging.getLogger(__name__)

_text_queue_semaphore: asyncio.Semaphore | None = None
_text_queue_limit: int | None = None
_image_queue_semaphore: asyncio.Semaphore | None = None
_image_queue_limit: int | None = None
_video_queue_semaphore: asyncio.Semaphore | None = None
_video_queue_limit: int | None = None
_callback_queue_semaphore: asyncio.Semaphore | None = None
_callback_queue_limit: int | None = None
_idempotency_locks_guard = Lock()
_idempotency_locks: dict[str, "_GenerationTaskIdempotencyLock"] = {}


@dataclass
class _GenerationTaskIdempotencyLock:
    lock: asyncio.Lock
    ref_count: int = 0


@dataclass(frozen=True)
class GenerationTaskListResult:
    items: list[GenerationTask]
    total: int
    limit: int
    offset: int
    summary: dict[str, Any]


@dataclass(frozen=True)
class GenerationTaskRecoveryTask:
    id: str
    queue_name: str
    priority: int = 0


@dataclass(frozen=True)
class GenerationTaskRecoveryResult:
    rescheduled_task_ids: list[str]
    interrupted_task_ids: list[str]
    stale_task_ids: list[str]
    rescheduled_tasks: list[GenerationTaskRecoveryTask] = field(default_factory=list)


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
        idempotency_key = _generation_task_idempotency_key(
            queue_name=queue_name,
            task_type=task_type,
            business_type=business_type,
            business_id=business_id,
            payload=payload,
        )
        async with _generation_task_idempotency_scope(idempotency_key):
            existing_task = await self._find_active_duplicate_task(
                session,
                queue_name=queue_name,
                task_type=task_type,
                business_type=business_type,
                business_id=business_id,
                idempotency_key=idempotency_key,
                payload=payload,
            )
            if existing_task is not None:
                await self._mark_idempotency_reuse(session, existing_task, idempotency_key)
                return existing_task

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
                metadata_json={
                    **(metadata or {}),
                    IDEMPOTENCY_KEY_METADATA_FIELD: idempotency_key,
                },
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            _set_generation_task_reused(task, False)
            return task

    async def get_task(self, session: AsyncSession, task_id: str) -> GenerationTask:
        return await get_required(session, GenerationTask, task_id)  # type: ignore[return-value]

    async def _find_active_duplicate_task(
        self,
        session: AsyncSession,
        *,
        queue_name: str,
        task_type: str,
        business_type: str,
        business_id: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> GenerationTask | None:
        tasks = list(
            (
                await session.execute(
                    select(GenerationTask)
                    .where(
                        GenerationTask.queue_name == queue_name,
                        GenerationTask.task_type == task_type,
                        GenerationTask.business_type == business_type,
                        GenerationTask.business_id == business_id,
                        GenerationTask.status.in_(ACTIVE_TASK_STATUSES),
                    )
                    .order_by(GenerationTask.queued_at.asc(), GenerationTask.created_at.asc())
                )
            )
            .scalars()
            .all()
        )
        for task in tasks:
            metadata = task.metadata_json or {}
            if metadata.get(IDEMPOTENCY_KEY_METADATA_FIELD) == idempotency_key:
                return task
            if _generation_task_payload_fingerprint(task.payload_json or {}) == (
                _generation_task_payload_fingerprint(payload)
            ):
                return task
        return None

    async def _mark_idempotency_reuse(
        self,
        session: AsyncSession,
        task: GenerationTask,
        idempotency_key: str,
    ) -> None:
        metadata = task.metadata_json or {}
        try:
            reuse_count = int(metadata.get("idempotency_reuse_count") or 0)
        except (TypeError, ValueError):
            reuse_count = 0
        task.metadata_json = {
            **metadata,
            IDEMPOTENCY_KEY_METADATA_FIELD: idempotency_key,
            "idempotency_reuse_count": reuse_count + 1,
            "idempotency_last_reused_at": utcnow().isoformat(),
        }
        await session.commit()
        await session.refresh(task)
        _set_generation_task_reused(task, True)

    async def list_tasks(
        self,
        session: AsyncSession,
        *,
        queue_name: str | None = None,
        status: str | None = None,
        task_type: str | None = None,
        business_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> GenerationTaskListResult:
        conditions = []
        if queue_name:
            conditions.append(GenerationTask.queue_name == queue_name)
        if status:
            conditions.append(GenerationTask.status == status)
        if task_type:
            conditions.append(GenerationTask.task_type == task_type)
        if business_id:
            conditions.append(GenerationTask.business_id == business_id)

        total = int(
            (
                await session.execute(
                    select(func.count()).select_from(GenerationTask).where(*conditions)
                )
            ).scalar_one()
            or 0
        )
        items = list(
            (
                await session.execute(
                    select(GenerationTask)
                    .where(*conditions)
                    .order_by(GenerationTask.queued_at.desc(), GenerationTask.created_at.desc())
                    .offset(max(offset, 0))
                    .limit(max(min(limit, 200), 1))
                )
            )
            .scalars()
            .all()
        )
        return GenerationTaskListResult(
            items=items,
            total=total,
            limit=max(min(limit, 200), 1),
            offset=max(offset, 0),
            summary=await self.task_summary(session),
        )

    async def task_summary(self, session: AsyncSession) -> dict[str, Any]:
        settings = get_settings()
        status_rows = (
            await session.execute(
                select(GenerationTask.status, func.count()).group_by(GenerationTask.status)
            )
        ).all()
        queue_rows = (
            await session.execute(
                select(GenerationTask.queue_name, func.count()).group_by(GenerationTask.queue_name)
            )
        ).all()
        task_type_rows = (
            await session.execute(
                select(GenerationTask.task_type, func.count()).group_by(GenerationTask.task_type)
            )
        ).all()
        all_tasks = list((await session.execute(select(GenerationTask))).scalars().all())
        total = int(
            (await session.execute(select(func.count()).select_from(GenerationTask))).scalar_one()
            or 0
        )
        retryable_failed_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(GenerationTask)
                    .where(GenerationTask.status == "failed", GenerationTask.retryable.is_(True))
                )
            ).scalar_one()
            or 0
        )
        active_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(GenerationTask)
                    .where(GenerationTask.status.in_(("queued", "running")))
                )
            ).scalar_one()
            or 0
        )
        resumable_queued_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(GenerationTask)
                    .where(GenerationTask.status == "queued")
                )
            ).scalar_one()
            or 0
        )
        running_stale_before = utcnow() - timedelta(
            seconds=get_settings().generation_task_running_stale_seconds
        )
        stale_running_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(GenerationTask)
                    .where(
                        GenerationTask.status == "running",
                        (
                            GenerationTask.started_at.is_(None)
                            | (GenerationTask.started_at <= running_stale_before)
                        ),
                    )
                )
            ).scalar_one()
            or 0
        )
        interrupted_failed_count = int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(GenerationTask)
                    .where(
                        GenerationTask.status == "failed",
                        GenerationTask.error_code.in_(("task_interrupted", "task_stale")),
                    )
                )
            ).scalar_one()
            or 0
        )
        queue_concurrency = _queue_concurrency_settings()
        queue_health = _generation_task_queue_health(all_tasks, queue_concurrency)
        return {
            "total": total,
            "by_status": {str(status): int(count) for status, count in status_rows},
            "by_queue": {str(queue_name): int(count) for queue_name, count in queue_rows},
            "by_task_type": {str(task_type): int(count) for task_type, count in task_type_rows},
            "retryable_failed_count": retryable_failed_count,
            "active_count": active_count,
            "resumable_queued_count": resumable_queued_count,
            "stale_running_count": stale_running_count,
            "interrupted_failed_count": interrupted_failed_count,
            "target_concurrent_users": settings.generation_task_target_concurrent_users,
            "total_active_capacity": sum(queue_concurrency.values()),
            "queue_concurrency": queue_concurrency,
            "queue_health": queue_health,
            "failure_codes": _generation_task_failure_codes(all_tasks),
            "slowest_queues": _generation_task_slowest_queues(queue_health),
        }

    async def recover_interrupted_tasks(
        self,
        session: AsyncSession,
        *,
        mark_running_interrupted: bool = True,
    ) -> GenerationTaskRecoveryResult:
        queued_tasks = await self._queued_tasks(session)
        running_tasks = (
            await self._running_tasks(session) if mark_running_interrupted else []
        )
        for task in running_tasks:
            self._mark_interrupted(
                task,
                error_code="task_interrupted",
                message=(
                    "Generation task was interrupted before completion. "
                    "The backend or worker may have restarted before processing finished."
                ),
            )
        await session.commit()
        return GenerationTaskRecoveryResult(
            rescheduled_task_ids=[task.id for task in queued_tasks],
            interrupted_task_ids=[task.id for task in running_tasks],
            stale_task_ids=[],
            rescheduled_tasks=_generation_task_recovery_refs(queued_tasks),
        )

    async def recover_stale_tasks(self, session: AsyncSession) -> GenerationTaskRecoveryResult:
        settings = get_settings()
        now = utcnow()
        queued_before = now - timedelta(seconds=settings.generation_task_queued_stale_seconds)
        running_before = now - timedelta(seconds=settings.generation_task_running_stale_seconds)

        queued_tasks = await self._queued_tasks(session, queued_before=queued_before)
        stale_tasks = await self._running_tasks(session, started_before=running_before)
        for task in stale_tasks:
            self._mark_interrupted(
                task,
                error_code="task_stale",
                message=(
                    "Generation task stayed running past the configured recovery timeout. "
                    "It may have stalled while calling the model provider."
                ),
            )
        await session.commit()
        return GenerationTaskRecoveryResult(
            rescheduled_task_ids=[task.id for task in queued_tasks],
            interrupted_task_ids=[],
            stale_task_ids=[task.id for task in stale_tasks],
            rescheduled_tasks=_generation_task_recovery_refs(queued_tasks),
        )

    async def _queued_tasks(
        self,
        session: AsyncSession,
        *,
        queued_before: datetime | None = None,
    ) -> list[GenerationTask]:
        conditions = [GenerationTask.status == "queued"]
        if queued_before is not None:
            conditions.append(GenerationTask.queued_at <= queued_before)
        return list(
            (
                await session.execute(
                    select(GenerationTask)
                    .where(*conditions)
                    .order_by(
                        GenerationTask.priority.desc(),
                        GenerationTask.queued_at.asc(),
                        GenerationTask.created_at.asc(),
                    )
                )
            )
            .scalars()
            .all()
        )

    async def _running_tasks(
        self,
        session: AsyncSession,
        *,
        started_before: datetime | None = None,
    ) -> list[GenerationTask]:
        conditions = [GenerationTask.status == "running"]
        if started_before is not None:
            conditions.append(
                GenerationTask.started_at.is_(None) | (GenerationTask.started_at <= started_before)
            )
        return list(
            (
                await session.execute(
                    select(GenerationTask)
                    .where(*conditions)
                    .order_by(GenerationTask.started_at.asc(), GenerationTask.created_at.asc())
                )
            )
            .scalars()
            .all()
        )

    def _mark_interrupted(
        self,
        task: GenerationTask,
        *,
        error_code: str,
        message: str,
    ) -> None:
        task.status = "failed"
        task.error_code = error_code
        task.error_message = message
        task.retryable = task.attempt_count < task.max_attempts
        task.finished_at = utcnow()
        task.duration_ms = _duration_ms(task.started_at, task.finished_at)

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
        queue_name = await self._task_queue_name(task_id)
        if queue_name == TEXT_QUEUE_NAME:
            async with _text_queue_capacity():
                await self._process_task_body(task_id)
            return
        if queue_name == VIDEO_QUEUE_NAME:
            async with _video_queue_capacity():
                await self._process_task_body(task_id)
            return
        if queue_name == CALLBACK_QUEUE_NAME:
            async with _callback_queue_capacity():
                await self._process_task_body(task_id)
            return
        await self._process_task_body(task_id)

    async def _task_queue_name(self, task_id: str) -> str | None:
        async with AsyncSessionLocal() as session:
            task = await self.get_task(session, task_id)
            return task.queue_name

    async def _process_task_body(self, task_id: str) -> None:
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

    async def run_in_image_queue(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with _image_queue_capacity():
            return await operation()

    async def run_in_video_queue(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with _video_queue_capacity():
            return await operation()

    async def run_in_callback_queue(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with _callback_queue_capacity():
            return await operation()

    async def _run_task(self, session: AsyncSession, task: GenerationTask) -> dict[str, Any]:
        if task.queue_name == TEXT_QUEUE_NAME and task.task_type in TEXT_TASK_TYPES:
            return await self._run_text_task(session, task)
        if task.queue_name == IMAGE_QUEUE_NAME and task.task_type in IMAGE_TASK_TYPES:
            return await self._run_image_task(session, task)
        if task.queue_name == VIDEO_QUEUE_NAME and task.task_type in VIDEO_TASK_TYPES:
            return await self._run_video_task(session, task)
        if task.queue_name == CALLBACK_QUEUE_NAME and task.task_type in CALLBACK_TASK_TYPES:
            return await self._run_callback_task(session, task)
        raise AppError(f"Unsupported generation task: {task.queue_name}/{task.task_type}")

    async def _run_text_task(self, session: AsyncSession, task: GenerationTask) -> dict[str, Any]:
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

    async def _run_image_task(self, session: AsyncSession, task: GenerationTask) -> dict[str, Any]:
        from backend.app.schemas.creative import CreativeGenerateRequest
        from backend.app.services.creative_service import CreativeService

        payload = task.payload_json or {}
        request = CreativeGenerateRequest.model_validate(payload)
        service = CreativeService()
        result = _initial_image_task_result(request)
        await self._update_task_result(session, task, result)

        async for event in service.stream_creatives(session, request):
            event_type = event.get("type")
            if event_type == "start":
                indices = event.get("indices")
                if isinstance(indices, list) and indices:
                    result = _initial_image_task_result(request, indices=indices)
                    await self._update_task_result(session, task, result)
                continue

            if event_type == "slot":
                index = _event_index(event)
                if index is not None:
                    _set_image_task_slot(result, index, {"status": "loading"})
                    await self._update_task_result(session, task, result)
                continue

            if event_type == "asset":
                index = _event_index(event)
                asset = event.get("asset")
                if index is not None and isinstance(asset, dict):
                    _record_image_task_asset(result, index, asset)
                    await self._update_task_result(session, task, result)
                continue

            if event_type == "error":
                index = _event_index(event)
                message = str(event.get("message") or "Image generation failed.")
                if index is not None:
                    _record_image_task_error(result, index, message)
                    await self._update_task_result(session, task, result)
                continue

            if event_type == "done":
                result["generated_count"] = int(event.get("generated") or result["generated_count"])

        _finalize_pending_image_task_slots(result)
        await self._update_task_result(session, task, result)
        if int(result.get("generated_count") or 0) <= 0:
            first_error = _first_image_task_error(result)
            raise AppError(first_error or "Image generation failed.")
        return result

    async def _run_video_task(self, session: AsyncSession, task: GenerationTask) -> dict[str, Any]:
        from backend.app.schemas.video import VideoAssetRead
        from backend.app.services.video_service import VideoService

        payload = task.payload_json or {}
        video_id = str(payload.get("video_id") or task.business_id or "")
        if not video_id:
            raise AppError("video_id is required for video generation tasks.")

        video = await VideoService().start_video_generation(session, video_id)
        serialized_video = VideoAssetRead.model_validate(video).model_dump(mode="json")
        return {
            "video_id": video.id,
            "status": video.status,
            "provider_job_id": video.provider_job_id,
            "video": serialized_video,
        }

    async def _run_callback_task(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> dict[str, Any]:
        from backend.app.db.models.ad_generation_job import AdGenerationJob
        from backend.app.services.ad_generation_service import AdGenerationService

        payload = task.payload_json or {}
        job_id = str(payload.get("job_id") or task.business_id or "")
        if not job_id:
            raise AppError("job_id is required for callback tasks.")

        job = await get_required(session, AdGenerationJob, job_id)
        callback_result = await AdGenerationService().deliver_callback(session, job)
        if callback_result is None:
            return {
                "job_id": job_id,
                "status": "skipped",
                "reason": "callback_url_missing",
            }

        result = {"job_id": job_id, **callback_result}
        if callback_result.get("status") != "succeeded":
            await self._update_task_result(session, task, result)
            raise AppError(str(callback_result.get("error") or "Callback delivery failed."))
        return result

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

    async def _update_task_result(
        self,
        session: AsyncSession,
        task: GenerationTask,
        result: dict[str, Any],
    ) -> None:
        task.result_json = result
        await session.commit()
        await session.refresh(task)


def _text_queue_capacity() -> asyncio.Semaphore:
    global _text_queue_limit, _text_queue_semaphore
    limit = get_settings().text_queue_concurrency
    if _text_queue_semaphore is None or _text_queue_limit != limit:
        _text_queue_semaphore = asyncio.Semaphore(limit)
        _text_queue_limit = limit
    return _text_queue_semaphore


def _image_queue_capacity() -> asyncio.Semaphore:
    global _image_queue_limit, _image_queue_semaphore
    limit = get_settings().image_queue_concurrency
    if _image_queue_semaphore is None or _image_queue_limit != limit:
        _image_queue_semaphore = asyncio.Semaphore(limit)
        _image_queue_limit = limit
    return _image_queue_semaphore


def _video_queue_capacity() -> asyncio.Semaphore:
    global _video_queue_limit, _video_queue_semaphore
    limit = get_settings().video_queue_concurrency
    if _video_queue_semaphore is None or _video_queue_limit != limit:
        _video_queue_semaphore = asyncio.Semaphore(limit)
        _video_queue_limit = limit
    return _video_queue_semaphore


def _callback_queue_capacity() -> asyncio.Semaphore:
    global _callback_queue_limit, _callback_queue_semaphore
    limit = get_settings().callback_queue_concurrency
    if _callback_queue_semaphore is None or _callback_queue_limit != limit:
        _callback_queue_semaphore = asyncio.Semaphore(limit)
        _callback_queue_limit = limit
    return _callback_queue_semaphore


def _queue_concurrency_settings() -> dict[str, int]:
    settings = get_settings()
    return {
        TEXT_QUEUE_NAME: settings.text_queue_concurrency,
        IMAGE_QUEUE_NAME: settings.image_queue_concurrency,
        VIDEO_QUEUE_NAME: settings.video_queue_concurrency,
        CALLBACK_QUEUE_NAME: settings.callback_queue_concurrency,
    }


def _generation_task_recovery_refs(
    tasks: list[GenerationTask],
) -> list[GenerationTaskRecoveryTask]:
    return [
        GenerationTaskRecoveryTask(
            id=task.id,
            queue_name=task.queue_name,
            priority=task.priority,
        )
        for task in tasks
    ]


def _generation_task_queue_health(
    tasks: list[GenerationTask],
    queue_concurrency: dict[str, int],
) -> dict[str, dict[str, Any]]:
    now = utcnow()
    queue_names = list(queue_concurrency)
    for task in tasks:
        if task.queue_name not in queue_names:
            queue_names.append(task.queue_name)

    health: dict[str, dict[str, Any]] = {}
    wait_values_by_queue: dict[str, list[int]] = {}
    run_values_by_queue: dict[str, list[int]] = {}
    for queue_name in queue_names:
        concurrency = max(int(queue_concurrency.get(queue_name, 1)), 1)
        health[queue_name] = {
            "total": 0,
            "queued": 0,
            "running": 0,
            "failed": 0,
            "succeeded": 0,
            "active": 0,
            "concurrency": concurrency,
            "backlog": 0,
            "pressure_ratio": 0.0,
            "risk_level": "low",
            "avg_wait_ms": None,
            "max_wait_ms": None,
            "avg_run_ms": None,
            "max_run_ms": None,
        }
        wait_values_by_queue[queue_name] = []
        run_values_by_queue[queue_name] = []

    for task in tasks:
        queue = health[task.queue_name]
        queue["total"] += 1
        if task.status in {"queued", "running", "failed", "succeeded"}:
            queue[task.status] += 1
        if task.status in ACTIVE_TASK_STATUSES:
            queue["active"] += 1

        wait_ms = _generation_task_wait_ms(task, now)
        if wait_ms is not None:
            wait_values_by_queue[task.queue_name].append(wait_ms)
        run_ms = _generation_task_run_ms(task, now)
        if run_ms is not None:
            run_values_by_queue[task.queue_name].append(run_ms)

    for queue_name, queue in health.items():
        wait_values = wait_values_by_queue[queue_name]
        run_values = run_values_by_queue[queue_name]
        concurrency = queue["concurrency"]
        queue["backlog"] = max(queue["active"] - concurrency, 0)
        queue["pressure_ratio"] = round(queue["active"] / concurrency, 2)
        queue["risk_level"] = _generation_task_queue_risk_level(queue)
        queue["avg_wait_ms"] = _average_int(wait_values)
        queue["max_wait_ms"] = max(wait_values) if wait_values else None
        queue["avg_run_ms"] = _average_int(run_values)
        queue["max_run_ms"] = max(run_values) if run_values else None
    return health


def _generation_task_failure_codes(tasks: list[GenerationTask]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for task in tasks:
        if task.status != "failed":
            continue
        code = task.error_code or "unknown"
        counts[code] = counts.get(code, 0) + 1
    return [
        {"code": code, "count": count}
        for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _generation_task_slowest_queues(
    queue_health: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [
        {
            "queue_name": queue_name,
            "avg_wait_ms": health["avg_wait_ms"],
            "max_wait_ms": health["max_wait_ms"],
            "avg_run_ms": health["avg_run_ms"],
            "max_run_ms": health["max_run_ms"],
            "risk_level": health["risk_level"],
        }
        for queue_name, health in queue_health.items()
        if health["avg_wait_ms"] is not None or health["avg_run_ms"] is not None
    ]
    return sorted(
        rows,
        key=lambda row: (
            -(row["avg_wait_ms"] or 0),
            -(row["avg_run_ms"] or 0),
            row["queue_name"],
        ),
    )


def _generation_task_queue_risk_level(queue: dict[str, Any]) -> str:
    if (
        queue["queued"] >= queue["concurrency"]
        or queue["backlog"] >= queue["concurrency"]
        or queue["pressure_ratio"] >= 2
    ):
        return "high"
    if queue["queued"] > 0 or queue["backlog"] > 0 or queue["pressure_ratio"] >= 1:
        return "medium"
    return "low"


async def recover_generation_tasks_on_startup(
    schedule_task: Callable[[str], Any] | None = None,
) -> GenerationTaskRecoveryResult:
    service = GenerationTaskService()
    async with AsyncSessionLocal() as session:
        result = await service.recover_interrupted_tasks(
            session,
            mark_running_interrupted=(
                get_settings().generation_task_execution_backend != "celery"
            ),
        )
    for task in result.rescheduled_tasks:
        _schedule_recovered_task(task, schedule_task)
    _log_recovery_result("startup", result)
    return result


async def run_generation_task_recovery_loop(
    schedule_task: Callable[[str], Any] | None = None,
) -> None:
    service = GenerationTaskService()
    while True:
        await asyncio.sleep(get_settings().generation_task_recovery_interval_seconds)
        try:
            async with AsyncSessionLocal() as session:
                result = await service.recover_stale_tasks(session)
            for task in result.rescheduled_tasks:
                _schedule_recovered_task(task, schedule_task)
            _log_recovery_result("periodic", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Generation task recovery loop failed.")


def _schedule_recovered_task(
    task: GenerationTaskRecoveryTask,
    schedule_task: Callable[[str], Any] | None,
) -> None:
    if schedule_task is not None:
        schedule_task(task.id)
        return
    from backend.app.services.generation_task_dispatcher import schedule_generation_task_id

    schedule_generation_task_id(
        task.id,
        queue_name=task.queue_name,
        priority=task.priority,
    )


def _log_recovery_result(source: str, result: GenerationTaskRecoveryResult) -> None:
    if not (
        result.rescheduled_task_ids or result.interrupted_task_ids or result.stale_task_ids
    ):
        return
    logger.info(
        "Generation task recovery completed.",
        extra={
            "source": source,
            "rescheduled_count": len(result.rescheduled_task_ids),
            "interrupted_count": len(result.interrupted_task_ids),
            "stale_count": len(result.stale_task_ids),
        },
    )


def generation_task_was_reused(task: GenerationTask) -> bool:
    return bool(getattr(task, "reused_existing", False))


def should_schedule_generation_task(task: GenerationTask) -> bool:
    return not generation_task_was_reused(task)


def _set_generation_task_reused(task: GenerationTask, reused: bool) -> None:
    task.reused_existing = reused


@asynccontextmanager
async def _generation_task_idempotency_scope(idempotency_key: str) -> AsyncIterator[None]:
    with _idempotency_locks_guard:
        entry = _idempotency_locks.get(idempotency_key)
        if entry is None:
            entry = _GenerationTaskIdempotencyLock(lock=asyncio.Lock())
            _idempotency_locks[idempotency_key] = entry
        entry.ref_count += 1
    try:
        async with entry.lock:
            yield
    finally:
        with _idempotency_locks_guard:
            entry.ref_count -= 1
            if entry.ref_count <= 0:
                _idempotency_locks.pop(idempotency_key, None)


def _generation_task_idempotency_key(
    *,
    queue_name: str,
    task_type: str,
    business_type: str,
    business_id: str,
    payload: dict[str, Any],
) -> str:
    raw = _canonical_json(
        {
            "queue_name": queue_name,
            "task_type": task_type,
            "business_type": business_type,
            "business_id": business_id,
            "payload": payload,
        }
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _generation_task_payload_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _initial_image_task_result(
    request: Any,
    indices: list[int] | None = None,
) -> dict[str, Any]:
    slot_indices = indices or (
        [request.target_index]
        if request.target_index is not None
        else list(range(1, request.count + 1))
    )
    return {
        "draft_id": request.draft_id,
        "total_count": len(slot_indices),
        "generated_count": 0,
        "failed_count": 0,
        "assets": [],
        "errors": [],
        "slots": [{"index": index, "status": "queued"} for index in slot_indices],
        "size": request.size,
        "generation_mode": request.generation_mode,
        "variant_count": request.variant_count,
        "frames_per_variant": request.frames_per_variant,
        "video_duration_seconds": request.video_duration_seconds,
    }


def _event_index(event: dict[str, Any]) -> int | None:
    try:
        index = int(event.get("index"))
    except (TypeError, ValueError):
        return None
    return index if index > 0 else None


def _set_image_task_slot(
    result: dict[str, Any],
    index: int,
    update: dict[str, Any],
) -> None:
    slots = result.setdefault("slots", [])
    for slot in slots:
        if isinstance(slot, dict) and slot.get("index") == index:
            slot.update(update)
            _refresh_image_task_counts(result)
            return
    slots.append({"index": index, **update})
    slots.sort(key=lambda item: int(item.get("index") or 0) if isinstance(item, dict) else 0)
    _refresh_image_task_counts(result)


def _record_image_task_asset(
    result: dict[str, Any],
    index: int,
    asset: dict[str, Any],
) -> None:
    assets = [
        item
        for item in result.get("assets", [])
        if not (isinstance(item, dict) and item.get("index") == index)
    ]
    assets.append({"index": index, "asset": asset})
    assets.sort(key=lambda item: int(item.get("index") or 0))
    result["assets"] = assets
    result["errors"] = [
        item
        for item in result.get("errors", [])
        if not (isinstance(item, dict) and item.get("index") == index)
    ]
    _set_image_task_slot(
        result,
        index,
        {"status": "done", "asset_id": asset.get("id"), "message": None},
    )


def _record_image_task_error(
    result: dict[str, Any],
    index: int,
    message: str,
) -> None:
    errors = [
        item
        for item in result.get("errors", [])
        if not (isinstance(item, dict) and item.get("index") == index)
    ]
    errors.append({"index": index, "message": message})
    errors.sort(key=lambda item: int(item.get("index") or 0))
    result["errors"] = errors
    _set_image_task_slot(result, index, {"status": "error", "message": message})


def _finalize_pending_image_task_slots(result: dict[str, Any]) -> None:
    for slot in result.get("slots", []):
        if not isinstance(slot, dict):
            continue
        if slot.get("status") in {"queued", "loading"}:
            slot["status"] = "error"
            slot["message"] = "Image generation did not finish."
    _refresh_image_task_counts(result)


def _refresh_image_task_counts(result: dict[str, Any]) -> None:
    slots = [slot for slot in result.get("slots", []) if isinstance(slot, dict)]
    result["generated_count"] = sum(1 for slot in slots if slot.get("status") == "done")
    result["failed_count"] = sum(1 for slot in slots if slot.get("status") == "error")


def _first_image_task_error(result: dict[str, Any]) -> str | None:
    for error in result.get("errors", []):
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    for slot in result.get("slots", []):
        if isinstance(slot, dict) and slot.get("message"):
            return str(slot["message"])
    return None


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


def _generation_task_wait_ms(task: GenerationTask, now: datetime) -> int | None:
    wait_until = task.started_at
    if wait_until is None and task.status in ACTIVE_TASK_STATUSES:
        wait_until = now
    return _duration_ms(task.queued_at, wait_until)


def _generation_task_run_ms(task: GenerationTask, now: datetime) -> int | None:
    if task.duration_ms is not None:
        return max(int(task.duration_ms), 0)
    if task.status == "running":
        return _duration_ms(task.started_at, now)
    return None


def _average_int(values: list[int]) -> int | None:
    if not values:
        return None
    return int(round(sum(values) / len(values)))
