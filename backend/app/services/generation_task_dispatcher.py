import asyncio
from typing import Protocol

from backend.app.core.config import get_settings
from backend.app.db.models.generation_task import GenerationTask


class _BackgroundTaskScheduler(Protocol):
    def add_task(self, func, *args) -> None: ...


class _GenerationTaskLike(Protocol):
    id: str
    queue_name: str
    priority: int


def schedule_generation_task(
    task: GenerationTask | _GenerationTaskLike,
    background_tasks: _BackgroundTaskScheduler | None = None,
) -> bool:
    if bool(getattr(task, "reused_existing", False)):
        return False
    return schedule_generation_task_id(
        str(task.id),
        queue_name=str(task.queue_name),
        background_tasks=background_tasks,
        priority=int(getattr(task, "priority", 0) or 0),
    )


def schedule_generation_task_id(
    task_id: str,
    *,
    queue_name: str,
    background_tasks: _BackgroundTaskScheduler | None = None,
    priority: int = 0,
) -> bool:
    settings = get_settings()
    if settings.generation_task_execution_backend == "celery":
        _enqueue_celery_generation_task(task_id, queue_name, priority)
        return True

    _enqueue_background_generation_task(task_id, background_tasks)
    return True


def schedule_ad_generation_job(
    job_id: str,
    background_tasks: _BackgroundTaskScheduler | None = None,
) -> bool:
    queue_name = "text_queue"
    settings = get_settings()
    if settings.generation_task_execution_backend == "celery":
        _enqueue_celery_ad_generation_job(job_id, queue_name)
        return True

    _enqueue_background_ad_generation_job(job_id, background_tasks)
    return True


def _enqueue_background_generation_task(
    task_id: str,
    background_tasks: _BackgroundTaskScheduler | None,
) -> None:
    from backend.app.services.generation_task_service import GenerationTaskService

    if background_tasks is not None:
        background_tasks.add_task(GenerationTaskService().process_task, task_id)
        return
    asyncio.create_task(GenerationTaskService().process_task(task_id))


def _enqueue_background_ad_generation_job(
    job_id: str,
    background_tasks: _BackgroundTaskScheduler | None,
) -> None:
    from backend.app.services.ad_generation_service import AdGenerationService

    if background_tasks is not None:
        background_tasks.add_task(AdGenerationService().run_job, job_id)
        return
    asyncio.create_task(AdGenerationService().run_job(job_id))


def _enqueue_celery_generation_task(task_id: str, queue_name: str, priority: int) -> None:
    from backend.app.worker.tasks import process_generation_task

    process_generation_task.apply_async(
        args=[task_id],
        queue=queue_name,
        priority=max(priority, 0),
    )


def _enqueue_celery_ad_generation_job(job_id: str, queue_name: str) -> None:
    from backend.app.worker.tasks import process_ad_generation_job

    process_ad_generation_job.apply_async(
        args=[job_id],
        queue=queue_name,
    )
