import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
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


@dataclass(frozen=True)
class GenerationTaskListResult:
    items: list[GenerationTask]
    total: int
    limit: int
    offset: int
    summary: dict[str, Any]


@dataclass(frozen=True)
class GenerationTaskRecoveryResult:
    rescheduled_task_ids: list[str]
    interrupted_task_ids: list[str]
    stale_task_ids: list[str]


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
        return {
            "total": total,
            "by_status": {str(status): int(count) for status, count in status_rows},
            "by_queue": {str(queue_name): int(count) for queue_name, count in queue_rows},
            "retryable_failed_count": retryable_failed_count,
            "active_count": active_count,
            "resumable_queued_count": resumable_queued_count,
            "stale_running_count": stale_running_count,
            "interrupted_failed_count": interrupted_failed_count,
        }

    async def recover_interrupted_tasks(
        self,
        session: AsyncSession,
    ) -> GenerationTaskRecoveryResult:
        queued_task_ids = await self._queued_task_ids(session)
        running_tasks = await self._running_tasks(session)
        for task in running_tasks:
            self._mark_interrupted(
                task,
                error_code="task_interrupted",
                message=(
                    "Generation task was interrupted before completion. "
                    "The backend may have restarted before BackgroundTasks finished."
                ),
            )
        await session.commit()
        return GenerationTaskRecoveryResult(
            rescheduled_task_ids=queued_task_ids,
            interrupted_task_ids=[task.id for task in running_tasks],
            stale_task_ids=[],
        )

    async def recover_stale_tasks(self, session: AsyncSession) -> GenerationTaskRecoveryResult:
        settings = get_settings()
        now = utcnow()
        queued_before = now - timedelta(seconds=settings.generation_task_queued_stale_seconds)
        running_before = now - timedelta(seconds=settings.generation_task_running_stale_seconds)

        queued_task_ids = await self._queued_task_ids(session, queued_before=queued_before)
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
            rescheduled_task_ids=queued_task_ids,
            interrupted_task_ids=[],
            stale_task_ids=[task.id for task in stale_tasks],
        )

    async def _queued_task_ids(
        self,
        session: AsyncSession,
        *,
        queued_before: datetime | None = None,
    ) -> list[str]:
        conditions = [GenerationTask.status == "queued"]
        if queued_before is not None:
            conditions.append(GenerationTask.queued_at <= queued_before)
        return [
            str(task_id)
            for task_id in (
                await session.execute(
                    select(GenerationTask.id)
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
        ]

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


async def recover_generation_tasks_on_startup(
    schedule_task: Callable[[str], Any] | None = None,
) -> GenerationTaskRecoveryResult:
    service = GenerationTaskService()
    async with AsyncSessionLocal() as session:
        result = await service.recover_interrupted_tasks(session)
    for task_id in result.rescheduled_task_ids:
        _schedule_recovered_task(task_id, schedule_task)
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
            for task_id in result.rescheduled_task_ids:
                _schedule_recovered_task(task_id, schedule_task)
            _log_recovery_result("periodic", result)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Generation task recovery loop failed.")


def _schedule_recovered_task(
    task_id: str,
    schedule_task: Callable[[str], Any] | None,
) -> None:
    if schedule_task is not None:
        schedule_task(task_id)
        return
    asyncio.create_task(GenerationTaskService().process_task(task_id))


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
