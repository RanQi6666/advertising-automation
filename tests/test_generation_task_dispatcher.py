from dataclasses import dataclass

import pytest

from backend.app.core.config import get_settings
from backend.app.services import generation_task_dispatcher as dispatcher


@dataclass
class FakeGenerationTask:
    id: str
    queue_name: str
    priority: int = 0
    reused_existing: bool = False


class FakeBackgroundTasks:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple[object, ...]]] = []

    def add_task(self, func, *args) -> None:
        self.calls.append((func, args))


def test_schedule_generation_task_uses_background_tasks_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GENERATION_TASK_EXECUTION_BACKEND", raising=False)
    get_settings.cache_clear()
    background_tasks = FakeBackgroundTasks()

    scheduled = dispatcher.schedule_generation_task(
        FakeGenerationTask(id="task-background-1", queue_name="text_queue"),
        background_tasks,
    )

    assert scheduled is True
    assert len(background_tasks.calls) == 1
    _, args = background_tasks.calls[0]
    assert args == ("task-background-1",)


def test_schedule_generation_task_skips_reused_existing_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    enqueued: list[tuple[str, str, int]] = []

    def capture_enqueue(task_id: str, queue_name: str, priority: int) -> None:
        enqueued.append((task_id, queue_name, priority))

    monkeypatch.setattr(dispatcher, "_enqueue_celery_generation_task", capture_enqueue)

    scheduled = dispatcher.schedule_generation_task(
        FakeGenerationTask(
            id="task-reused-1",
            queue_name="image_queue",
            reused_existing=True,
        )
    )

    assert scheduled is False
    assert enqueued == []


def test_schedule_generation_task_uses_celery_queue_from_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    enqueued: list[tuple[str, str, int]] = []

    def capture_enqueue(task_id: str, queue_name: str, priority: int) -> None:
        enqueued.append((task_id, queue_name, priority))

    monkeypatch.setattr(dispatcher, "_enqueue_celery_generation_task", capture_enqueue)

    scheduled = dispatcher.schedule_generation_task(
        FakeGenerationTask(id="task-celery-1", queue_name="image_queue", priority=5)
    )

    assert scheduled is True
    assert enqueued == [("task-celery-1", "image_queue", 5)]


def test_schedule_generation_task_id_uses_explicit_queue_in_celery_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    enqueued: list[tuple[str, str, int]] = []

    def capture_enqueue(task_id: str, queue_name: str, priority: int) -> None:
        enqueued.append((task_id, queue_name, priority))

    monkeypatch.setattr(dispatcher, "_enqueue_celery_generation_task", capture_enqueue)

    scheduled = dispatcher.schedule_generation_task_id(
        "task-callback-1",
        queue_name="callback_queue",
        priority=3,
    )

    assert scheduled is True
    assert enqueued == [("task-callback-1", "callback_queue", 3)]


def test_schedule_ad_generation_job_uses_background_tasks_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GENERATION_TASK_EXECUTION_BACKEND", raising=False)
    get_settings.cache_clear()
    background_tasks = FakeBackgroundTasks()

    scheduled = dispatcher.schedule_ad_generation_job(
        "job-background-1",
        background_tasks,
    )

    assert scheduled is True
    assert len(background_tasks.calls) == 1
    _, args = background_tasks.calls[0]
    assert args == ("job-background-1",)


def test_schedule_ad_generation_job_uses_text_queue_in_celery_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    enqueued: list[tuple[str, str]] = []

    def capture_enqueue(job_id: str, queue_name: str) -> None:
        enqueued.append((job_id, queue_name))

    monkeypatch.setattr(dispatcher, "_enqueue_celery_ad_generation_job", capture_enqueue)

    scheduled = dispatcher.schedule_ad_generation_job("job-celery-1")

    assert scheduled is True
    assert enqueued == [("job-celery-1", "text_queue")]
