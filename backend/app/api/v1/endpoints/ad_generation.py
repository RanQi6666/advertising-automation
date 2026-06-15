from fastapi import APIRouter, BackgroundTasks, status

from backend.app.api.deps import DbSession
from backend.app.schemas.ad_generation import (
    PublishingAdGenerationJobAccepted,
    PublishingAdGenerationJobCreate,
    PublishingAdGenerationJobRead,
)
from backend.app.services.ad_generation_service import AdGenerationService

router = APIRouter()
service = AdGenerationService()


@router.post(
    "/integrations/publishing/ad-generation/jobs",
    response_model=PublishingAdGenerationJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_publishing_ad_generation_job(
    payload: PublishingAdGenerationJobCreate,
    background_tasks: BackgroundTasks,
    session: DbSession,
):
    job = await service.create_job(session, payload)
    background_tasks.add_task(service.run_job, job.id)
    return PublishingAdGenerationJobAccepted(job_id=job.id, status=job.status)


@router.get(
    "/integrations/publishing/ad-generation/jobs/{job_id}",
    response_model=PublishingAdGenerationJobRead,
)
async def get_publishing_ad_generation_job(job_id: str, session: DbSession):
    return await service.get_job(session, job_id)
