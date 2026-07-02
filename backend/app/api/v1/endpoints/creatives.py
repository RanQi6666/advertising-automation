import json

from fastapi import APIRouter, BackgroundTasks, Query, status
from starlette.responses import StreamingResponse

from backend.app.api.deps import CurrentOperator, DbSession
from backend.app.core.errors import AppError
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.schemas.creative import (
    CreativeAssetRead,
    CreativeGenerateRequest,
    CreativeRegenerateRequest,
)
from backend.app.schemas.generation_task import GenerationTaskRead
from backend.app.services.creative_service import CreativeService
from backend.app.services.generation_attempt_service import GenerationAttemptService
from backend.app.services.generation_task_dispatcher import schedule_generation_task
from backend.app.services.generation_task_service import (
    IMAGE_QUEUE_NAME,
    GenerationTaskService,
)
from backend.app.services.utils import get_required

router = APIRouter()
service = CreativeService()
attempt_service = GenerationAttemptService()
task_service = GenerationTaskService()


@router.post(
    "/creatives/generate",
    response_model=list[CreativeAssetRead],
    status_code=status.HTTP_201_CREATED,
)
async def generate_creatives(payload: CreativeGenerateRequest, session: DbSession):
    return await service.generate_creatives(session, payload)


@router.post(
    "/creatives/generate/task",
    response_model=GenerationTaskRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_generate_creatives(
    payload: CreativeGenerateRequest,
    session: DbSession,
    operator: CurrentOperator,
    background_tasks: BackgroundTasks,
):
    draft = await get_required(session, CopyDraft, payload.draft_id)
    task = await task_service.create_task(
        session,
        queue_name=IMAGE_QUEUE_NAME,
        task_type="image_generate",
        business_type="copy_draft",
        business_id=payload.draft_id,
        campaign_id=draft.campaign_id,
        payload=payload.model_dump(mode="json"),
        owner_user_id=operator.id,
        max_attempts=3,
        metadata={
            "size": payload.size,
            "target_index": payload.target_index,
            "target_indices": payload.target_indices,
            "generation_mode": payload.generation_mode,
            "variant_count": payload.variant_count,
            "frames_per_variant": payload.frames_per_variant,
            "video_duration_seconds": payload.video_duration_seconds,
        },
    )
    schedule_generation_task(task, background_tasks)
    return GenerationTaskRead.from_model(task)


@router.post(
    "/creatives/generate/keyframe-tasks",
    response_model=list[GenerationTaskRead],
    status_code=status.HTTP_202_ACCEPTED,
)
async def queue_generate_keyframe_creatives(
    payload: CreativeGenerateRequest,
    session: DbSession,
    operator: CurrentOperator,
    background_tasks: BackgroundTasks,
):
    if payload.generation_mode != "video_keyframe_variants":
        raise AppError("Keyframe batch tasks require video_keyframe_variants mode.")
    if payload.target_index is not None or payload.target_indices:
        raise AppError("Keyframe batch tasks compute target_indices automatically.")

    draft = await get_required(session, CopyDraft, payload.draft_id)
    total_count = payload.variant_count * payload.frames_per_variant
    brief_payload = payload.model_copy(
        update={
            "count": total_count,
            "target_index": None,
            "target_indices": [],
            "prepared_briefs": [],
        }
    )
    prepared_briefs = await service.prepare_image_briefs(session, brief_payload)
    tasks = []
    for target_indices in _keyframe_task_target_groups(
        payload.variant_count,
        payload.frames_per_variant,
    ):
        prepared_subset = [
            brief for brief in prepared_briefs if brief.image_index in set(target_indices)
        ]
        if len(prepared_subset) != len(target_indices):
            raise AppError("Prepared image briefs are missing keyframe slots.")
        task_payload = payload.model_copy(
            update={
                "count": len(target_indices),
                "target_index": None,
                "target_indices": target_indices,
                "prepared_briefs": prepared_subset,
            }
        )
        task = await task_service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id=payload.draft_id,
            campaign_id=draft.campaign_id,
            payload=task_payload.model_dump(mode="json"),
            owner_user_id=operator.id,
            max_attempts=3,
            metadata={
                "size": payload.size,
                "target_index": None,
                "target_indices": target_indices,
                "generation_mode": payload.generation_mode,
                "variant_count": payload.variant_count,
                "frames_per_variant": payload.frames_per_variant,
                "video_duration_seconds": payload.video_duration_seconds,
                "prepared_briefs": True,
            },
        )
        tasks.append(task)

    for task in tasks:
        schedule_generation_task(task, background_tasks)
    return [GenerationTaskRead.from_model(task) for task in tasks]


@router.post("/creatives/generate/stream")
async def stream_creatives(payload: CreativeGenerateRequest, session: DbSession):
    total_count = (
        len(payload.target_indices)
        if payload.target_indices
        else 1
        if payload.target_index is not None
        else payload.count
    )
    attempt = await attempt_service.create_attempt(
        session,
        business_type="image",
        business_id=payload.draft_id,
        stage="creative_image_generation",
        total_count=total_count,
        model=payload.model_id,
        metadata={
            "size": payload.size,
            "target_index": payload.target_index,
            "target_indices": payload.target_indices,
            "generation_mode": payload.generation_mode,
            "variant_count": payload.variant_count,
            "frames_per_variant": payload.frames_per_variant,
            "video_duration_seconds": payload.video_duration_seconds,
        },
    )

    async def event_stream():
        async for event in attempt_service.track_stream(
            session=session,
            attempt=attempt,
            events=service.stream_creatives(session, payload),
            success_event_types={"asset"},
        ):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _keyframe_task_target_groups(
    variant_count: int,
    frames_per_variant: int,
) -> list[list[int]]:
    return [
        [
            variant_index * frames_per_variant + frame_index + 1
            for frame_index in range(frames_per_variant)
        ]
        for variant_index in range(variant_count)
    ]


@router.post("/creatives/{creative_id}/regenerate", response_model=CreativeAssetRead)
async def regenerate_creative(
    creative_id: str,
    payload: CreativeRegenerateRequest,
    session: DbSession,
):
    return await service.regenerate_creative(
        session=session,
        creative_id=creative_id,
        feedback=payload.feedback,
        size=payload.size,
        image_model_id=payload.model_id,
    )


@router.get("/campaigns/{campaign_id}/creatives", response_model=list[CreativeAssetRead])
async def list_creatives(
    campaign_id: str,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_creatives(
        session, campaign_id=campaign_id, limit=limit, offset=offset
    )
