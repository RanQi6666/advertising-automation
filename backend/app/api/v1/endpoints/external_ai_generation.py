from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from backend.app.api.deps import DbSession, require_ai_ads_access_token
from backend.app.core.config import get_settings
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
from backend.app.services.video_storage_service import VideoStorageService

REFERENCE_VIDEO_UPLOAD_CONTENT_TYPES = {
    "video/mp4",
    "video/quicktime",
    "video/webm",
}
REFERENCE_VIDEO_UPLOAD_READ_CHUNK_BYTES = 1024 * 1024


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


@router.post(
    "/storyboard-v2/reference-video",
    response_model=ExternalAIGenerationEnvelope,
    status_code=status.HTTP_201_CREATED,
)
async def upload_frame_anchored_storyboard_reference_video(
    video: Annotated[UploadFile, File(...)],
):
    try:
        content_type = (video.content_type or "").split(";", 1)[0].strip().lower()
        if content_type not in REFERENCE_VIDEO_UPLOAD_CONTENT_TYPES:
            raise AppError("Reference video must be MP4, MOV, or WebM.")
        video_data = await _read_reference_video_upload(
            video,
            max_bytes=get_settings().video_download_max_bytes,
        )
        if not video_data:
            raise AppError("Uploaded reference video is empty.")
        upload_asset_id = VideoStorageService().store_uploaded_reference_video(
            video_data,
            content_type,
            video.filename,
        )
        return JSONResponse(
            status_code=status.HTTP_201_CREATED,
            content=ExternalAIGenerationEnvelope(
                code=AI_GENERATION_CODE_SUCCESS,
                message="success",
                data={"upload_asset_id": upload_asset_id},
            ).model_dump(),
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


async def _read_reference_video_upload(
    video: UploadFile,
    *,
    max_bytes: int,
    chunk_size: int = REFERENCE_VIDEO_UPLOAD_READ_CHUNK_BYTES,
) -> bytes:
    chunks: list[bytes] = []
    total_bytes = 0
    while True:
        chunk = await video.read(chunk_size)
        if not chunk:
            return b"".join(chunks)
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise AppError("Uploaded reference video exceeds configured size limit.")
        chunks.append(chunk)


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
