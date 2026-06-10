from fastapi import APIRouter, Query, status

from backend.app.api.deps import DbSession
from backend.app.schemas.insight import InsightDailyCreate, InsightDailyRead
from backend.app.services.insight_service import InsightService

router = APIRouter()
service = InsightService()


@router.post(
    "/insights/daily", response_model=InsightDailyRead, status_code=status.HTTP_201_CREATED
)
async def create_daily_insight(payload: InsightDailyCreate, session: DbSession):
    return await service.create_daily(session, payload)


@router.get("/campaigns/{campaign_id}/insights/daily", response_model=list[InsightDailyRead])
async def list_daily_insights(
    campaign_id: str,
    session: DbSession,
    limit: int = Query(default=90, ge=1, le=365),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_daily(session, campaign_id=campaign_id, limit=limit, offset=offset)
