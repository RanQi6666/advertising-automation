from fastapi import APIRouter, BackgroundTasks, Query

from backend.app.api.deps import CurrentOperator, DbSession
from backend.app.schemas.generation_task import GenerationTaskListResponse, GenerationTaskRead
from backend.app.services.generation_task_dispatcher import schedule_generation_task
from backend.app.services.generation_task_service import GenerationTaskService

router = APIRouter()
service = GenerationTaskService()


@router.get("/generation-tasks", response_model=GenerationTaskListResponse)
async def list_generation_tasks(
    session: DbSession,
    operator: CurrentOperator,
    queue_name: str | None = Query(default=None),
    status: str | None = Query(default=None),
    task_type: str | None = Query(default=None),
    business_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    result = await service.list_tasks(
        session,
        queue_name=queue_name,
        status=status,
        task_type=task_type,
        business_id=business_id,
        operator=operator,
        limit=limit,
        offset=offset,
    )
    return GenerationTaskListResponse.from_result(result)


@router.get("/generation-tasks/{task_id}", response_model=GenerationTaskRead)
async def get_generation_task(task_id: str, session: DbSession, operator: CurrentOperator):
    task = await service.get_task(session, task_id, operator=operator)
    return GenerationTaskRead.from_model(task)


@router.post("/generation-tasks/{task_id}/retry", response_model=GenerationTaskRead)
async def retry_generation_task(
    task_id: str,
    session: DbSession,
    operator: CurrentOperator,
    background_tasks: BackgroundTasks,
):
    task = await service.retry_task(session, task_id, operator=operator)
    schedule_generation_task(task, background_tasks)
    return GenerationTaskRead.from_model(task)
