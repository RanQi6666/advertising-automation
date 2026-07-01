from fastapi import APIRouter, BackgroundTasks

from backend.app.api.deps import DbSession
from backend.app.schemas.generation_task import GenerationTaskRead
from backend.app.services.generation_task_service import GenerationTaskService

router = APIRouter()
service = GenerationTaskService()


@router.get("/generation-tasks/{task_id}", response_model=GenerationTaskRead)
async def get_generation_task(task_id: str, session: DbSession):
    task = await service.get_task(session, task_id)
    return GenerationTaskRead.from_model(task)


@router.post("/generation-tasks/{task_id}/retry", response_model=GenerationTaskRead)
async def retry_generation_task(
    task_id: str,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    task = await service.retry_task(session, task_id)
    background_tasks.add_task(service.process_task, task.id)
    return GenerationTaskRead.from_model(task)
