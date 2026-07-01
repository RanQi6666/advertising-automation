import json

from fastapi import APIRouter, BackgroundTasks, Query, status
from starlette.responses import StreamingResponse

from backend.app.api.deps import DbSession
from backend.app.schemas.generation_task import GenerationTaskRead
from backend.app.schemas.topic import TopicGenerateRequest, TopicRead
from backend.app.services.generation_attempt_service import GenerationAttemptService
from backend.app.services.generation_task_service import (
    TEXT_QUEUE_NAME,
    GenerationTaskService,
    should_schedule_generation_task,
)
from backend.app.services.topic_service import TopicService

router = APIRouter()
service = TopicService()
attempt_service = GenerationAttemptService()
task_service = GenerationTaskService()


@router.post(
    "/topics/generate", response_model=list[TopicRead], status_code=status.HTTP_201_CREATED
)
async def generate_topics(payload: TopicGenerateRequest, session: DbSession):
    return await service.generate_topics(session, payload)


@router.post(
    "/topics/generate/task", response_model=GenerationTaskRead, status_code=status.HTTP_202_ACCEPTED
)
async def queue_generate_topics(
    payload: TopicGenerateRequest,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    task = await task_service.create_task(
        session,
        queue_name=TEXT_QUEUE_NAME,
        task_type="topic_generate",
        business_type="campaign",
        business_id=payload.campaign_id,
        campaign_id=payload.campaign_id,
        payload=payload.model_dump(mode="json"),
        metadata={"limit": payload.limit},
    )
    if should_schedule_generation_task(task):
        background_tasks.add_task(task_service.process_task, task.id)
    return GenerationTaskRead.from_model(task)


@router.post("/topics/generate/stream")
async def stream_topics(payload: TopicGenerateRequest, session: DbSession):
    attempt = await attempt_service.create_attempt(
        session,
        business_type="topic",
        business_id=payload.campaign_id,
        campaign_id=payload.campaign_id,
        stage="topic_generation",
        total_count=payload.limit,
        model=payload.model_id,
        metadata={"signals": payload.signals},
    )

    async def event_stream():
        async for event in attempt_service.track_stream(
            session=session,
            attempt=attempt,
            events=service.stream_topics(session, payload),
            success_event_types={"topic"},
        ):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/campaigns/{campaign_id}/topics", response_model=list[TopicRead])
async def list_topics(
    campaign_id: str,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_topics(session, campaign_id=campaign_id, limit=limit, offset=offset)


@router.post("/topics/{topic_id}/select", response_model=TopicRead)
async def select_topic(topic_id: str, session: DbSession):
    return await service.select_topic(session, topic_id)


@router.post("/topics/{topic_id}/reject", response_model=TopicRead)
async def reject_topic(topic_id: str, session: DbSession):
    return await service.reject_topic(session, topic_id)
