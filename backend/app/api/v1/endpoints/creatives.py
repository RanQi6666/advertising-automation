import json

from fastapi import APIRouter, Query, status
from starlette.responses import StreamingResponse

from backend.app.api.deps import DbSession
from backend.app.schemas.creative import (
    CreativeAssetRead,
    CreativeGenerateRequest,
    CreativeRegenerateRequest,
)
from backend.app.services.creative_service import CreativeService

router = APIRouter()
service = CreativeService()


@router.post(
    "/creatives/generate",
    response_model=list[CreativeAssetRead],
    status_code=status.HTTP_201_CREATED,
)
async def generate_creatives(payload: CreativeGenerateRequest, session: DbSession):
    return await service.generate_creatives(session, payload)


@router.post("/creatives/generate/stream")
async def stream_creatives(payload: CreativeGenerateRequest, session: DbSession):
    async def event_stream():
        async for event in service.stream_creatives(session, payload):
            yield json.dumps(event, ensure_ascii=False) + "\n"

    return StreamingResponse(
        event_stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
