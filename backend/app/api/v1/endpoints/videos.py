from fastapi import APIRouter, Query, status

from backend.app.api.deps import DbSession
from backend.app.schemas.video import VideoAssetRead, VideoGenerateRequest
from backend.app.services.video_service import VideoService

router = APIRouter()
service = VideoService()


@router.post(
    "/videos/from-images",
    response_model=VideoAssetRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_video_from_images(payload: VideoGenerateRequest, session: DbSession):
    return await service.create_video_job(session, payload)


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
