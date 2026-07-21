from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from fastapi.responses import JSONResponse

from backend.app.api.deps import DbSession
from backend.app.core.errors import AppError, NotFoundError
from backend.app.schemas.ad_research import (
    AdResearchCreateRequest,
    AdResearchCreateResponse,
    AdResearchPollResponse,
)
from backend.app.services.ad_research_service import (
    AdResearchIdempotencyConflict,
    AdResearchResultExpired,
    AdResearchService,
)
from backend.app.services.generation_task_dispatcher import schedule_ad_research_job

router = APIRouter()
service = AdResearchService()


@router.post(
    "/integrations/ad-research/jobs",
    response_model=AdResearchCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_ad_research_job(
    payload: AdResearchCreateRequest,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    try:
        result = await service.create_job(session, payload)
    except AdResearchIdempotencyConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "external_user_id_payload_conflict", "message": str(exc)},
        ) from exc

    if result.task is not None:
        try:
            schedule_ad_research_job(result.task.id, background_tasks)
        except Exception as exc:  # persist a retryable, non-sensitive dispatch failure
            await service.mark_dispatch_failure(session, result.job, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "queue_dispatch_failed",
                    "message": "task dispatch failed; retry same external_user_id",
                },
            ) from exc

    response = AdResearchCreateResponse(
        task_id=result.job.id,
        external_user_id=payload.external_user_id,
        status=result.job.status,
        idempotent_replay=result.idempotent_replay,
        poll_url=f"/api/v1/integrations/ad-research/jobs/{result.job.id}",
    )
    if result.idempotent_replay:
        return JSONResponse(
            status_code=status.HTTP_200_OK, content=response.model_dump(mode="json")
        )
    return response


@router.get("/integrations/ad-research/jobs/{task_id}", response_model=AdResearchPollResponse)
async def get_ad_research_job(task_id: str, session: DbSession) -> AdResearchPollResponse:
    try:
        job = await service.get_job(session, task_id)
    except AdResearchResultExpired as exc:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail={"code": "result_expired", "message": str(exc)},
        ) from exc
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "task_not_found", "message": str(exc)},
        ) from exc
    except AppError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return service.poll_response(job)
