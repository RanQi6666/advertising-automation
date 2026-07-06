from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from backend.app.api.deps import DbSession, require_ai_ads_access_token
from backend.app.core.errors import AppError, NotFoundError, ProviderError
from backend.app.schemas.external_video_generation import (
    VIDEO_GENERATION_CODE_PROCESSING,
    VIDEO_GENERATION_CODE_PROVIDER_ERROR,
    VIDEO_GENERATION_CODE_SUCCESS,
    VIDEO_GENERATION_CODE_VALIDATION_ERROR,
    ExternalVideoGenerationCreate,
    ExternalVideoGenerationEnvelope,
    ExternalVideoGenerationJobRead,
)
from backend.app.services.external_video_generation_service import (
    ExternalVideoGenerationService,
)
from backend.app.services.generation_task_dispatcher import schedule_generation_task


class ExternalVideoGenerationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                return await original_route_handler(request)
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "code": VIDEO_GENERATION_CODE_VALIDATION_ERROR,
                        "message": "request validation failed",
                        "data": {"errors": jsonable_encoder(exc.errors())},
                    },
                )

        return custom_route_handler


router = APIRouter(
    prefix="/integrations/video-generation",
    dependencies=[Depends(require_ai_ads_access_token)],
    route_class=ExternalVideoGenerationRoute,
)


@router.post(
    "/videos",
    response_model=ExternalVideoGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_video_generation_job(
    payload: ExternalVideoGenerationCreate,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    try:
        job, task = await _service().create_video(session, payload)
        if task is not None:
            schedule_generation_task(task, background_tasks)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=_job_envelope(job).model_dump(),
        )
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=VIDEO_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=VIDEO_GENERATION_CODE_VALIDATION_ERROR,
        )


@router.get("/jobs/{job_id}", response_model=ExternalVideoGenerationEnvelope)
async def get_video_generation_job(job_id: str, session: DbSession):
    try:
        return _job_envelope(await _service().get_job(session, job_id))
    except NotFoundError as exc:
        return _error_response(
            exc,
            status.HTTP_404_NOT_FOUND,
            code=VIDEO_GENERATION_CODE_VALIDATION_ERROR,
        )
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=VIDEO_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=VIDEO_GENERATION_CODE_VALIDATION_ERROR,
        )


def _service() -> ExternalVideoGenerationService:
    return ExternalVideoGenerationService()


def _job_envelope(job: ExternalVideoGenerationJobRead) -> ExternalVideoGenerationEnvelope:
    data = {
        "job_id": job.job_id,
        "status": job.status,
        "duration_seconds": job.duration_seconds,
        "aspect_ratio": job.aspect_ratio,
    }
    if job.status == "succeeded":
        data["url"] = job.video_url
        return ExternalVideoGenerationEnvelope(
            code=VIDEO_GENERATION_CODE_SUCCESS,
            message="success",
            data=data,
        )
    if job.status == "failed":
        data["error"] = job.error_message
        return ExternalVideoGenerationEnvelope(
            code=VIDEO_GENERATION_CODE_PROVIDER_ERROR,
            message="video generation failed",
            data=data,
        )
    return ExternalVideoGenerationEnvelope(
        code=VIDEO_GENERATION_CODE_PROCESSING,
        message="processing",
        data=data,
    )


def _error_response(exc: Exception, status_code: int, *, code: int) -> JSONResponse:
    if isinstance(exc, HTTPException):
        message = exc.detail
    else:
        message = str(exc)
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": {}},
    )
