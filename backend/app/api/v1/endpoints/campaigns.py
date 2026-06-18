from fastapi import APIRouter, Query, status

from backend.app.api.deps import DbSession
from backend.app.schemas.campaign import (
    BrandCreate,
    BrandRead,
    CampaignCreate,
    CampaignRead,
    ClientCreate,
    ClientRead,
)
from backend.app.services.campaign_service import CampaignService

router = APIRouter()
service = CampaignService()


@router.post("/clients", response_model=ClientRead, status_code=status.HTTP_201_CREATED)
async def create_client(payload: ClientCreate, session: DbSession):
    return await service.create_client(session, payload)


@router.get("/clients", response_model=list[ClientRead])
async def list_clients(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_clients(session, limit=limit, offset=offset)


@router.post("/brands", response_model=BrandRead, status_code=status.HTTP_201_CREATED)
async def create_brand(payload: BrandCreate, session: DbSession):
    return await service.create_brand(session, payload)


@router.post("/campaigns", response_model=CampaignRead, status_code=status.HTTP_201_CREATED)
async def create_campaign(payload: CampaignCreate, session: DbSession):
    return await service.create_campaign(session, payload)


@router.get("/campaigns", response_model=list[CampaignRead])
async def list_campaigns(
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_campaigns(session, limit=limit, offset=offset)


@router.get("/campaigns/{campaign_id}", response_model=CampaignRead)
async def get_campaign(campaign_id: str, session: DbSession):
    return await service.get_campaign(session, campaign_id)
