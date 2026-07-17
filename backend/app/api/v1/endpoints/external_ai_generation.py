from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from backend.app.api.deps import DbSession, require_ai_ads_access_token
from backend.app.core.errors import AppError, NotFoundError, ProviderError
from backend.app.db.models.generation_task import GenerationTask
from backend.app.schemas.external_ai_generation import (
    AI_GENERATION_CODE_PROCESSING,
    AI_GENERATION_CODE_PROVIDER_ERROR,
    AI_GENERATION_CODE_SUCCESS,
    AI_GENERATION_CODE_VALIDATION_ERROR,
    ExternalAICopyGenerationCreate,
    ExternalAIFrameAnchoredStoryboardCreate,
    ExternalAIGenerationEnvelope,
    ExternalAITopicSelectionCreate,
    ExternalAIVideoStoryboardCreate,
    ExternalAIWorkOrderAnalysisCreate,
)
from backend.app.services.external_ai_generation_service import (
    EXTERNAL_AI_TEXT_TASK_TYPES,
    ExternalAIGenerationService,
)
from backend.app.services.generation_task_dispatcher import schedule_generation_task
from backend.app.services.llm_rate_limit import get_cached_generation_task_status


class ExternalAIGenerationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                return await original_route_handler(request)
            except RequestValidationError as exc:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={
                        "code": AI_GENERATION_CODE_VALIDATION_ERROR,
                        "message": "request validation failed",
                        "data": {"errors": jsonable_encoder(exc.errors())},
                    },
                )

        return custom_route_handler


router = APIRouter(
    prefix="/integrations/ai",
    dependencies=[Depends(require_ai_ads_access_token)],
    route_class=ExternalAIGenerationRoute,
)


@router.post(
    "/work-order-analysis",
    response_model=ExternalAIGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_work_order_analysis_job(
    payload: ExternalAIWorkOrderAnalysisCreate,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    try:
        task = await _service().create_work_order_analysis_job(session, payload)
        schedule_generation_task(task, background_tasks)
        return _accepted_response(task)
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=AI_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=AI_GENERATION_CODE_VALIDATION_ERROR,
        )


@router.post(
    "/topics",
    response_model=ExternalAIGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_topic_selection_job(
    payload: ExternalAITopicSelectionCreate,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    try:
        task = await _service().create_topic_selection_job(session, payload)
        schedule_generation_task(task, background_tasks)
        return _accepted_response(task)
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=AI_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=AI_GENERATION_CODE_VALIDATION_ERROR,
        )


@router.post(
    "/copy",
    response_model=ExternalAIGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_copy_generation_job(
    payload: ExternalAICopyGenerationCreate,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    try:
        task = await _service().create_copy_generation_job(session, payload)
        schedule_generation_task(task, background_tasks)
        return _accepted_response(task)
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=AI_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=AI_GENERATION_CODE_VALIDATION_ERROR,
        )


@router.post(
    "/storyboard",
    response_model=ExternalAIGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_video_storyboard_job(
    payload: ExternalAIVideoStoryboardCreate,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    try:
        task = await _service().create_video_storyboard_job(session, payload)
        schedule_generation_task(task, background_tasks)
        return _accepted_response(task)
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=AI_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=AI_GENERATION_CODE_VALIDATION_ERROR,
        )


@router.post(
    "/storyboard-v2",
    response_model=ExternalAIGenerationEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_frame_anchored_video_storyboard_job(
    payload: ExternalAIFrameAnchoredStoryboardCreate,
    session: DbSession,
    background_tasks: BackgroundTasks,
):
    try:
        task = await _service().create_frame_anchored_video_storyboard_job(session, payload)
        schedule_generation_task(task, background_tasks)
        return _accepted_response(task)
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=AI_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=AI_GENERATION_CODE_VALIDATION_ERROR,
        )


@router.get("/jobs/{job_id}", response_model=ExternalAIGenerationEnvelope)
async def get_ai_generation_job(job_id: str, session: DbSession):
    try:
        cached = await get_cached_generation_task_status(job_id)
        if cached and cached.get("task_type") in EXTERNAL_AI_TEXT_TASK_TYPES:
            return _cached_job_envelope(cached)
        return _job_envelope(await _service().get_job(session, job_id))
    except NotFoundError as exc:
        return _error_response(
            exc,
            status.HTTP_404_NOT_FOUND,
            code=AI_GENERATION_CODE_VALIDATION_ERROR,
        )
    except ProviderError as exc:
        return _error_response(
            exc,
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            code=AI_GENERATION_CODE_PROVIDER_ERROR,
        )
    except AppError as exc:
        return _error_response(
            exc,
            status.HTTP_400_BAD_REQUEST,
            code=AI_GENERATION_CODE_VALIDATION_ERROR,
        )


def _service() -> ExternalAIGenerationService:
    return ExternalAIGenerationService()


def _accepted_response(task: GenerationTask) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content=ExternalAIGenerationEnvelope(
            code=AI_GENERATION_CODE_PROCESSING,
            message="processing",
            data={
                "asy_task_id": task.id,
                "job_id": task.id,
                "status": "processing",
                "retry_after_seconds": 2,
            },
        ).model_dump(),
    )


def _job_envelope(task: GenerationTask) -> ExternalAIGenerationEnvelope:
    data = {
        "asy_task_id": task.id,
        "job_id": task.id,
        "status": _external_status(task),
    }
    if task.status == "succeeded":
        data.update(task.result_json or {})
        return ExternalAIGenerationEnvelope(
            code=AI_GENERATION_CODE_SUCCESS,
            message="success",
            data=data,
        )
    if task.status == "failed":
        data["error"] = task.error_message
        return ExternalAIGenerationEnvelope(
            code=AI_GENERATION_CODE_PROVIDER_ERROR,
            message="ai generation failed",
            data=data,
        )
    return ExternalAIGenerationEnvelope(
        code=AI_GENERATION_CODE_PROCESSING,
        message="processing",
        data={**data, "retry_after_seconds": 2},
    )


def _cached_job_envelope(cached: dict) -> ExternalAIGenerationEnvelope:
    job_id = str(cached["job_id"])
    status_value = str(cached.get("status") or "failed")
    data = {
        "asy_task_id": job_id,
        "job_id": job_id,
        "status": status_value,
    }
    if status_value == "succeeded":
        result_json = cached.get("result_json")
        if isinstance(result_json, dict):
            data.update(result_json)
        return ExternalAIGenerationEnvelope(
            code=AI_GENERATION_CODE_SUCCESS,
            message="success",
            data=data,
        )
    data["error"] = cached.get("error_message")
    return ExternalAIGenerationEnvelope(
        code=AI_GENERATION_CODE_PROVIDER_ERROR,
        message="ai generation failed",
        data=data,
    )


def _external_status(task: GenerationTask) -> str:
    if task.status == "succeeded":
        return "succeeded"
    if task.status == "failed":
        return "failed"
    return "processing"


def _error_response(exc: Exception, status_code: int, *, code: int) -> JSONResponse:
    if isinstance(exc, HTTPException):
        message = exc.detail
    else:
        message = str(exc)
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "data": {}},
    )
