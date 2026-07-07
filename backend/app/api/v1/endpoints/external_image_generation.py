from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from backend.app.api.deps import DbSession, require_ai_ads_access_token
from backend.app.core.errors import AppError, NotFoundError, ProviderError
from backend.app.schemas.external_image_generation import (
    IMAGE_GENERATION_CODE_PROCESSING,
    IMAGE_GENERATION_CODE_PROVIDER_ERROR,
    IMAGE_GENERATION_CODE_SUCCESS,
    IMAGE_GENERATION_CODE_VALIDATION_ERROR,
    ExternalImageGenerationCreate,
    ExternalImageGenerationEnvelope,
    ExternalImageGenerationJobRead,
    ExternalImageRevisionCreate,
)
from backend.app.services.external_image_generation_service import (
    ExternalImageGenerationService,
)
from backend.app.services.generation_task_dispatcher import schedule_generation_task


class ExternalImageGenerationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                return await original_route_handler(request)
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "code": IMAGE_GENERATION_CODE_VALIDATION_ERROR,
                        "message": "request validation failed",
                        "data": {"errors": jsonable_encoder(exc.errors())},
                    },
                )

        return custom_route_handler


router = APIRouter(
    prefix="/integrations/image-generation",
    dependencies=[Depends(require_ai_ads_access_token)],
    route_class=ExternalImageGenerationRoute,
)


@router.post(
    "/images",
    response_model=ExternalImageGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_image_generation_job(
    payload: ExternalImageGenerationCreate,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    try:
        task = await _service().create_job(session, payload)
        schedule_generation_task(task, background_tasks)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=_creation_envelope(
                job_id=task.id,
                count=int((task.payload_json or {}).get("count") or payload.count),
                size=str((task.payload_json or {}).get("size") or payload.size),
            ).model_dump(),
        )
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=IMAGE_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=IMAGE_GENERATION_CODE_VALIDATION_ERROR,
        )


@router.post(
    "/jobs/{source_job_id}/revisions",
    response_model=ExternalImageGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_image_revision_job(
    source_job_id: str,
    payload: ExternalImageRevisionCreate,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    try:
        task = await _service().create_revision_job(session, source_job_id, payload)
        schedule_generation_task(task, background_tasks)
        task_payload = task.payload_json or {}
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content=_creation_envelope(
                job_id=task.id,
                count=int(task_payload.get("count") or 1),
                size=str(task_payload.get("size") or "1:1"),
                source_job_id=str(task_payload.get("source_job_id") or source_job_id),
                mode=_clean_mode((task.metadata_json or {}).get("mode")),
            ).model_dump(),
        )
    except NotFoundError as exc:
        return _error_response(
            exc,
            status.HTTP_404_NOT_FOUND,
            code=IMAGE_GENERATION_CODE_VALIDATION_ERROR,
        )
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=IMAGE_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=IMAGE_GENERATION_CODE_VALIDATION_ERROR,
        )


@router.get("/jobs/{job_id}", response_model=ExternalImageGenerationEnvelope)
async def get_image_generation_job(job_id: str, session: DbSession):
    try:
        return _job_envelope(await _service().get_job(session, job_id))
    except NotFoundError as exc:
        return _error_response(
            exc,
            status.HTTP_404_NOT_FOUND,
            code=IMAGE_GENERATION_CODE_VALIDATION_ERROR,
        )
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=IMAGE_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=IMAGE_GENERATION_CODE_VALIDATION_ERROR,
        )


def _service() -> ExternalImageGenerationService:
    return ExternalImageGenerationService()


def _creation_envelope(
    *,
    job_id: str,
    count: int,
    size: str,
    source_job_id: str | None = None,
    mode: str | None = None,
) -> ExternalImageGenerationEnvelope:
    data = {
        "job_id": job_id,
        "status": "processing",
        "count": count,
        "size": size,
    }
    if source_job_id:
        data["source_job_id"] = source_job_id
    if mode:
        data["mode"] = mode
    return ExternalImageGenerationEnvelope(
        code=IMAGE_GENERATION_CODE_PROCESSING,
        message="processing",
        data=data,
    )


def _job_envelope(job: ExternalImageGenerationJobRead) -> ExternalImageGenerationEnvelope:
    data = {
        "job_id": job.job_id,
        "status": job.status,
        "count": job.count,
        "size": job.size,
        "images": [image.model_dump() for image in job.images],
    }
    if job.model_id:
        data["model_id"] = job.model_id
    if job.source_job_id:
        data["source_job_id"] = job.source_job_id
    if job.mode:
        data["mode"] = job.mode
    if job.status == "succeeded":
        return ExternalImageGenerationEnvelope(
            code=IMAGE_GENERATION_CODE_SUCCESS,
            message="success",
            data=data,
        )
    if job.status == "failed":
        data["error"] = job.error_message
        return ExternalImageGenerationEnvelope(
            code=IMAGE_GENERATION_CODE_PROVIDER_ERROR,
            message="image generation failed",
            data=data,
        )
    return ExternalImageGenerationEnvelope(
        code=IMAGE_GENERATION_CODE_PROCESSING,
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


def _clean_mode(value: object) -> str | None:
    text = str(value or "").strip()
    return text if text in {"edit", "generate"} else None
