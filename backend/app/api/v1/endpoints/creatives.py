from fastapi import APIRouter, Query, status

from backend.app.api.deps import DbSession
from backend.app.schemas.creative import CreativeAssetRead, CreativeGenerateRequest
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
