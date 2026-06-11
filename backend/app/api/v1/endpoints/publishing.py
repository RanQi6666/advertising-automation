from fastapi import APIRouter, Query, status

from backend.app.api.deps import DbSession
from backend.app.schemas.publishing import (
    AdCreativeDraftRead,
    AdCreativeDraftRequest,
    AdsPlanDraftRead,
    AdsPlanDraftRequest,
    FacebookPublishConfigRead,
    MetaAdsDraftCreateRead,
    MetaAdsDraftCreateRequest,
    MetaAdsPackagePrepareRequest,
    PublishJobCreate,
    PublishJobRead,
)
from backend.app.services.publish_service import PublishService

router = APIRouter()
service = PublishService()


@router.get("/publishing/meta-config", response_model=FacebookPublishConfigRead)
async def get_meta_publish_config():
    return service.get_meta_config()


@router.post("/publishing/ad-creative-draft", response_model=AdCreativeDraftRead)
async def build_ad_creative_draft(payload: AdCreativeDraftRequest, session: DbSession):
    return await service.build_ad_creative_draft(session, payload)


@router.post("/publishing/ads-plan-draft", response_model=AdsPlanDraftRead)
async def build_ads_plan_draft(payload: AdsPlanDraftRequest, session: DbSession):
    return await service.build_ads_plan_draft(session, payload)


@router.post("/publishing/meta-ads-draft", response_model=MetaAdsDraftCreateRead)
async def create_meta_ads_draft(payload: MetaAdsDraftCreateRequest, session: DbSession):
    return await service.create_meta_ads_draft(session, payload)


@router.post(
    "/publishing/meta-ads-package",
    response_model=PublishJobRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_meta_ads_package(payload: MetaAdsPackagePrepareRequest, session: DbSession):
    return await service.prepare_meta_ads_package(session, payload)


@router.post("/publishing/jobs", response_model=PublishJobRead, status_code=status.HTTP_201_CREATED)
async def create_publish_job(payload: PublishJobCreate, session: DbSession):
    return await service.create_job(session, payload)


@router.get("/publishing/jobs", response_model=list[PublishJobRead])
async def list_publish_jobs(
    session: DbSession,
    campaign_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_jobs(session, campaign_id=campaign_id, limit=limit, offset=offset)


@router.post("/publishing/jobs/{job_id}/publish", response_model=PublishJobRead)
async def publish_job(job_id: str, session: DbSession):
    return await service.publish_job(session, job_id)
