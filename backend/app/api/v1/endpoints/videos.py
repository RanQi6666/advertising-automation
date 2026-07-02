import json

from fastapi import APIRouter, BackgroundTasks, Query, status
from starlette.responses import StreamingResponse

from backend.app.api.deps import CurrentOperator, DbSession
from backend.app.db.models.video_asset import VideoAsset
from backend.app.schemas.generation_task import GenerationTaskRead
from backend.app.schemas.video import (
    VideoAssetRead,
    VideoGenerateRequest,
    VideoStoryboardGenerateRequest,
    VideoStoryboardRead,
    VideoStoryboardRewriteRequest,
)
from backend.app.services.generation_attempt_service import GenerationAttemptService
from backend.app.services.generation_task_dispatcher import schedule_generation_task
from backend.app.services.generation_task_service import (
    TEXT_QUEUE_NAME,
    VIDEO_QUEUE_NAME,
    GenerationTaskService,
)
from backend.app.services.utils import get_required
from backend.app.services.video_service import VideoService

router = APIRouter()
service = VideoService()
attempt_service = GenerationAttemptService()
task_service = GenerationTaskService()


@router.post(
    "/videos/storyboard",
    response_model=VideoStoryboardRead,
    status_code=status.HTTP_201_CREATED,
)
async def generate_video_storyboard(payload: VideoStoryboardGenerateRequest, session: DbSession):
    return await service.generate_storyboard(session, payload)


@router.post("/videos/storyboard/stream")
async def stream_video_storyboard(payload: VideoStoryboardGenerateRequest, session: DbSession):
    attempt = await attempt_service.create_attempt(
        session,
        business_type="video_storyboard",
        business_id=payload.draft_id or payload.campaign_id,
        campaign_id=payload.campaign_id,
        stage="video_storyboard_generation",
        total_count=1,
        model=payload.model_id,
        metadata={
            "creative_asset_ids": payload.creative_asset_ids,
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
        },
    )

    async def event_stream():
        async for event in attempt_service.track_stream(
            session=session,
            attempt=attempt,
            events=service.stream_storyboard_text(session, payload),
            success_event_types={"done"},
            store_last_success_event=True,
        ):
            yield _sse_event(event.get("type", "message"), event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/videos/storyboard/task",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_video_storyboard(
    payload: VideoStoryboardGenerateRequest,
    session: DbSession,
    operator: CurrentOperator,
    background_tasks: BackgroundTasks,
):
    business_id = payload.draft_id or payload.campaign_id
    task = await task_service.create_task(
        session,
        queue_name=TEXT_QUEUE_NAME,
        task_type="video_storyboard_generate",
        business_type="copy_draft" if payload.draft_id else "campaign",
        business_id=business_id,
        campaign_id=payload.campaign_id,
        payload=payload.model_dump(mode="json"),
        owner_user_id=operator.id,
        max_attempts=3,
        metadata={
            "creative_asset_ids": payload.creative_asset_ids,
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
            "draft_id": payload.draft_id,
            "model_id": payload.model_id,
        },
    )
    schedule_generation_task(task, background_tasks)
    return GenerationTaskRead.from_model(task)


@router.post(
    "/videos/storyboard/rewrite",
    response_model=VideoStoryboardRead,
    status_code=status.HTTP_201_CREATED,
)
async def rewrite_video_storyboard(payload: VideoStoryboardRewriteRequest, session: DbSession):
    return await service.rewrite_storyboard(session, payload)


@router.post("/videos/storyboard/rewrite/stream")
async def stream_rewrite_video_storyboard(
    payload: VideoStoryboardRewriteRequest,
    session: DbSession,
):
    attempt = await attempt_service.create_attempt(
        session,
        business_type="video_storyboard",
        business_id=payload.draft_id or payload.campaign_id,
        campaign_id=payload.campaign_id,
        stage="video_storyboard_rewrite",
        total_count=1,
        model=payload.model_id,
        metadata={
            "creative_asset_ids": payload.creative_asset_ids,
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
        },
    )

    async def event_stream():
        async for event in attempt_service.track_stream(
            session=session,
            attempt=attempt,
            events=service.stream_rewrite_storyboard_text(session, payload),
            success_event_types={"done"},
            store_last_success_event=True,
        ):
            yield _sse_event(event.get("type", "message"), event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/videos/storyboard/rewrite/task",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_rewrite_video_storyboard(
    payload: VideoStoryboardRewriteRequest,
    session: DbSession,
    operator: CurrentOperator,
    background_tasks: BackgroundTasks,
):
    business_id = payload.draft_id or payload.campaign_id
    task = await task_service.create_task(
        session,
        queue_name=TEXT_QUEUE_NAME,
        task_type="video_storyboard_rewrite",
        business_type="copy_draft" if payload.draft_id else "campaign",
        business_id=business_id,
        campaign_id=payload.campaign_id,
        payload=payload.model_dump(mode="json"),
        owner_user_id=operator.id,
        max_attempts=3,
        metadata={
            "creative_asset_ids": payload.creative_asset_ids,
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
            "draft_id": payload.draft_id,
            "model_id": payload.model_id,
        },
    )
    schedule_generation_task(task, background_tasks)
    return GenerationTaskRead.from_model(task)


@router.post(
    "/videos/from-images",
    response_model=VideoAssetRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_video_from_images(payload: VideoGenerateRequest, session: DbSession):
    return await service.create_video_job(session, payload)


@router.post("/videos/{video_id}/generate", response_model=VideoAssetRead)
async def start_video_generation(video_id: str, session: DbSession):
    return await service.start_video_generation(session, video_id)


@router.post(
    "/videos/{video_id}/generate/task",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_start_video_generation(
    video_id: str,
    session: DbSession,
    operator: CurrentOperator,
    background_tasks: BackgroundTasks,
):
    video = await get_required(session, VideoAsset, video_id)
    task = await task_service.create_task(
        session,
        queue_name=VIDEO_QUEUE_NAME,
        task_type="video_generate",
        business_type="video_asset",
        business_id=video.id,
        campaign_id=video.campaign_id,
        payload={"video_id": video.id},
        owner_user_id=operator.id,
        metadata={
            "duration_seconds": video.duration_seconds,
            "aspect_ratio": video.aspect_ratio,
            "source_asset_ids": video.source_asset_ids,
        },
    )
    schedule_generation_task(task, background_tasks)
    return GenerationTaskRead.from_model(task)


@router.post("/videos/{video_id}/refresh", response_model=VideoAssetRead)
async def refresh_video_generation(video_id: str, session: DbSession):
    return await service.refresh_video_generation(session, video_id)


@router.get("/campaigns/{campaign_id}/videos", response_model=list[VideoAssetRead])
async def list_videos(
    campaign_id: str,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_videos(
        session,
        campaign_id=campaign_id,
        limit=limit,
        offset=offset,
    )


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
