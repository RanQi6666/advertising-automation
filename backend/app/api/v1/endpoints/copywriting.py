from fastapi import APIRouter, BackgroundTasks, Query, status

from backend.app.api.deps import DbSession
from backend.app.schemas.copywriting import CopyDraftRead, CopyGenerateRequest, CopyReviseRequest
from backend.app.schemas.generation_task import GenerationTaskRead
from backend.app.services.copywriting_service import CopywritingService
from backend.app.services.generation_task_service import TEXT_QUEUE_NAME, GenerationTaskService

router = APIRouter()
service = CopywritingService()
task_service = GenerationTaskService()


@router.post(
    "/copywriting/generate", response_model=CopyDraftRead, status_code=status.HTTP_201_CREATED
)
async def generate_copy(payload: CopyGenerateRequest, session: DbSession):
    return await service.generate_copy(session, payload)


@router.post(
    "/copywriting/generate/task",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_generate_copy(
    payload: CopyGenerateRequest,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    task = await task_service.create_task(
        session,
        queue_name=TEXT_QUEUE_NAME,
        task_type="copy_generate",
        business_type="topic",
        business_id=payload.topic_id,
        payload=payload.model_dump(mode="json"),
    )
    background_tasks.add_task(task_service.process_task, task.id)
    return GenerationTaskRead.from_model(task)


@router.post("/copywriting/{draft_id}/revise", response_model=CopyDraftRead)
async def revise_copy(draft_id: str, payload: CopyReviseRequest, session: DbSession):
    return await service.revise_copy(session, draft_id, payload)


@router.post(
    "/copywriting/{draft_id}/revise/task",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_revise_copy(
    draft_id: str,
    payload: CopyReviseRequest,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    task = await task_service.create_task(
        session,
        queue_name=TEXT_QUEUE_NAME,
        task_type="copy_revise",
        business_type="copy_draft",
        business_id=draft_id,
        payload={"draft_id": draft_id, "request": payload.model_dump(mode="json")},
    )
    background_tasks.add_task(task_service.process_task, task.id)
    return GenerationTaskRead.from_model(task)


@router.get("/campaigns/{campaign_id}/drafts", response_model=list[CopyDraftRead])
async def list_drafts(
    campaign_id: str,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_drafts(session, campaign_id=campaign_id, limit=limit, offset=offset)
