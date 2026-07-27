from backend.app.services.generation_task_service import (
    GenerationTaskRecoveryResult,
    GenerationTaskRecoveryTask,
)
from backend.app.worker import tasks


def test_recovery_routes_ad_research_to_its_dedicated_celery_task(monkeypatch) -> None:
    scheduled_research: list[str] = []
    scheduled_generic: list[str] = []

    class FakeGenerationTaskService:
        async def recover_stale_tasks(self, session):
            return GenerationTaskRecoveryResult(
                rescheduled_task_ids=["research-task", "text-task"],
                interrupted_task_ids=[],
                stale_task_ids=[],
                rescheduled_tasks=[
                    GenerationTaskRecoveryTask(
                        id="research-task", queue_name="ad_research_queue", task_type="ad_research"
                    ),
                    GenerationTaskRecoveryTask(
                        id="text-task", queue_name="text_queue", task_type="topic_generate"
                    ),
                ],
            )

    monkeypatch.setattr(tasks, "GenerationTaskService", FakeGenerationTaskService)
    monkeypatch.setattr(
        tasks, "schedule_ad_research_job", lambda task_id: scheduled_research.append(task_id)
    )
    monkeypatch.setattr(
        tasks,
        "schedule_generation_task_id",
        lambda task_id, *, queue_name, priority: scheduled_generic.append(task_id),
    )

    tasks._run_async(tasks._recover_stale_generation_tasks())

    assert scheduled_research == ["research-task"]
    assert scheduled_generic == ["text-task"]

def test_application_recovery_routes_ad_research_to_its_dedicated_dispatcher(monkeypatch) -> None:
    from backend.app.services import (
        generation_task_dispatcher,
        generation_task_service,
    )

    scheduled_research: list[str] = []
    scheduled_generic: list[str] = []
    monkeypatch.setattr(
        generation_task_dispatcher,
        "schedule_ad_research_job",
        lambda task_id, background_tasks=None: scheduled_research.append(task_id),
    )
    monkeypatch.setattr(
        generation_task_dispatcher,
        "schedule_generation_task_id",
        lambda task_id, *, queue_name, priority: scheduled_generic.append(task_id),
    )

    generation_task_service._schedule_recovered_task(
        GenerationTaskRecoveryTask(
            id="research-task", queue_name="ad_research_queue", task_type="ad_research"
        ),
        schedule_task=None,
    )
    generation_task_service._schedule_recovered_task(
        GenerationTaskRecoveryTask(
            id="text-task", queue_name="text_queue", task_type="topic_generate"
        ),
        schedule_task=None,
    )

    assert scheduled_research == ["research-task"]
    assert scheduled_generic == ["text-task"]
