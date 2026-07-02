import asyncio

from backend.app.worker import tasks


def test_worker_task_runner_reuses_event_loop_between_tasks(monkeypatch) -> None:
    loops: list[asyncio.AbstractEventLoop] = []

    class FakeGenerationTaskService:
        async def process_task(self, task_id: str) -> None:
            loops.append(asyncio.get_running_loop())

    monkeypatch.setattr(tasks, "GenerationTaskService", FakeGenerationTaskService)
    close_worker_event_loop = getattr(tasks, "_close_worker_event_loop", lambda: None)
    close_worker_event_loop()

    try:
        tasks.process_generation_task("task-one")
        tasks.process_generation_task("task-two")

        assert len(loops) == 2
        assert loops[0] is loops[1]
        assert loops[0].is_closed() is False
    finally:
        close_worker_event_loop()
