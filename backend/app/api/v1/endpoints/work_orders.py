from fastapi import APIRouter, Query, status

from backend.app.api.deps import DbSession
from backend.app.schemas.campaign import CampaignRead
from backend.app.schemas.work_order import (
    CampaignFromWorkOrderRequest,
    WorkOrderCreate,
    WorkOrderRead,
)
from backend.app.services.campaign_service import CampaignService
from backend.app.services.work_order_service import WorkOrderService

router = APIRouter()
work_order_service = WorkOrderService()
campaign_service = CampaignService()


@router.post("/work-orders", response_model=WorkOrderRead, status_code=status.HTTP_201_CREATED)
async def create_work_order(payload: WorkOrderCreate, session: DbSession):
    return await work_order_service.create_work_order(session, payload)


@router.get("/work-orders", response_model=list[WorkOrderRead])
async def list_work_orders(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await work_order_service.list_work_orders(
        session,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/work-orders/{work_order_id}/campaign",
    response_model=CampaignRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_campaign_from_work_order(
    work_order_id: str,
    payload: CampaignFromWorkOrderRequest,
    session: DbSession,
):
    return await campaign_service.create_campaign_from_work_order(
        session,
        work_order_id=work_order_id,
        payload=payload,
    )
