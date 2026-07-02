import asyncio
import logging
from collections.abc import Awaitable
from contextlib import suppress
from typing import TypeVar

from celery.signals import worker_process_shutdown

from backend.app.services.ad_generation_service import AdGenerationService
from backend.app.services.generation_task_service import GenerationTaskService
from backend.app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)
T = TypeVar("T")
_worker_event_loop: asyncio.AbstractEventLoop | None = None


@celery_app.task(name="generation_tasks.process", ignore_result=True)
def process_generation_task(task_id: str) -> None:
    _run_async(GenerationTaskService().process_task(task_id))


@celery_app.task(name="ad_generation_jobs.process", ignore_result=True)
def process_ad_generation_job(job_id: str) -> None:
    _run_async(AdGenerationService().run_job(job_id))


def _run_async(awaitable: Awaitable[T]) -> T:
    loop = _get_worker_event_loop()
    return loop.run_until_complete(awaitable)


def _get_worker_event_loop() -> asyncio.AbstractEventLoop:
    global _worker_event_loop
    if _worker_event_loop is None or _worker_event_loop.is_closed():
        _worker_event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_event_loop)
    return _worker_event_loop


def _close_worker_event_loop() -> None:
    global _worker_event_loop
    loop = _worker_event_loop
    _worker_event_loop = None
    if loop is None or loop.is_closed():
        return
    with suppress(Exception):
        loop.run_until_complete(_dispose_async_resources())
    loop.close()


async def _dispose_async_resources() -> None:
    from backend.app.db.session import engine

    await engine.dispose()


@worker_process_shutdown.connect
def _on_worker_process_shutdown(**_: object) -> None:
    try:
        _close_worker_event_loop()
    except Exception:
        logger.exception("Failed to close Celery worker event loop.")
