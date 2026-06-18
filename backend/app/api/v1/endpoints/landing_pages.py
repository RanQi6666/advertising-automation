from fastapi import APIRouter, Query, status

from backend.app.api.deps import DbSession
from backend.app.schemas.landing_page import (
    LandingPageAnalyzeRequest,
    LandingPageSnapshotRead,
)
from backend.app.services.landing_page_service import LandingPageService

router = APIRouter()
service = LandingPageService()


@router.post(
    "/campaigns/{campaign_id}/landing-page/analyze",
    response_model=LandingPageSnapshotRead,
    status_code=status.HTTP_202_ACCEPTED,
)
async def analyze_landing_page(
    campaign_id: str,
    payload: LandingPageAnalyzeRequest,
    session: DbSession,
):
    return await service.analyze_campaign_landing_page(session, campaign_id, payload)


@router.get(
    "/campaigns/{campaign_id}/landing-page/snapshots",
    response_model=list[LandingPageSnapshotRead],
)
async def list_landing_page_snapshots(
    campaign_id: str,
    session: DbSession,
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_snapshots(
        session,
        campaign_id=campaign_id,
        limit=limit,
        offset=offset,
    )
