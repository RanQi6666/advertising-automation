from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from backend.app.api.deps import DbSession
from backend.app.db.models.ad_generation_job import AdGenerationJob
from backend.app.schemas.ad_generation import (
    PublishingAdGenerationJobAccepted,
    PublishingAdGenerationJobCreate,
    PublishingAdGenerationJobRead,
    PublishingAdGenerationReviewConfirm,
    PublishingAdGenerationReviewUpdate,
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
    return PublishingAdGenerationJobAccepted(
        job_id=job.id,
        status=job.status,
        review_url=service.review_url_for_job(job.id),
    )


@router.get(
    "/integrations/publishing/ad-generation/jobs",
    response_model=list[PublishingAdGenerationJobRead],
)
async def list_publishing_ad_generation_jobs(
    session: DbSession,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    jobs = await service.list_jobs(
        session,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return [_job_read(job) for job in jobs]


@router.get(
    "/integrations/publishing/ad-generation/jobs/{job_id}",
    response_model=PublishingAdGenerationJobRead,
)
async def get_publishing_ad_generation_job(job_id: str, session: DbSession):
    return _job_read(await service.get_job(session, job_id))


@router.get(
    "/integrations/publishing/ad-generation/jobs/{job_id}/result",
    response_model=dict[str, Any],
)
async def get_publishing_ad_generation_result(job_id: str, session: DbSession):
    job = await service.get_job(session, job_id)
    if job.status != "returned":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Final ad generation result is not ready.",
        )
    return _result_payload(job)


@router.delete(
    "/integrations/publishing/ad-generation/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_publishing_ad_generation_job(job_id: str, session: DbSession):
    await service.delete_job(session, job_id)


@router.patch(
    "/integrations/publishing/ad-generation/jobs/{job_id}/review",
    response_model=PublishingAdGenerationJobRead,
)
async def update_publishing_ad_generation_review(
    job_id: str,
    payload: PublishingAdGenerationReviewUpdate,
    session: DbSession,
):
    return _job_read(await service.update_review_payload(session, job_id, payload))


@router.post(
    "/integrations/publishing/ad-generation/jobs/{job_id}/confirm",
    response_model=PublishingAdGenerationJobRead,
)
async def confirm_publishing_ad_generation_review(
    job_id: str,
    payload: PublishingAdGenerationReviewConfirm,
    session: DbSession,
):
    return _job_read(await service.confirm_review(session, job_id, payload))


def _job_read(job: AdGenerationJob) -> PublishingAdGenerationJobRead:
    read = PublishingAdGenerationJobRead.model_validate(job)
    metadata = job.metadata_json or {}
    read.review_url = str(metadata.get("review_url") or service.review_url_for_job(job.id))
    return_url = metadata.get("return_url")
    read.return_url = str(return_url) if return_url else None
    return read


def _result_payload(job: AdGenerationJob) -> dict[str, Any]:
    payload = dict(job.result_payload or {})
    payload["job_id"] = job.id
    payload["external_order_id"] = job.external_order_id
    payload["status"] = job.status
    return payload
