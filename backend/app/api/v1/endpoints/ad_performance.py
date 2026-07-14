import json
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Query, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.responses import StreamingResponse

from backend.app.api.deps import CurrentOperator, DbSession, OptionalOperator
from backend.app.core.errors import AppError, NotFoundError
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.schemas.ad_performance import (
    AdPerformanceAnalysisCreate,
    AdPerformanceAnalysisRead,
)
from backend.app.schemas.external_ad_performance_analysis import (
    AD_ANALYSIS_CODE_ACCEPTED,
    AD_ANALYSIS_CODE_SERVER_ERROR,
    AD_ANALYSIS_CODE_SUCCESS,
    AD_ANALYSIS_CODE_VALIDATION_ERROR,
    AdAnalysisCreateData,
    AdAnalysisEnvelope,
    AdAnalysisJobData,
    AdAnalysisJobError,
    ExternalAdPerformanceAnalysisCreate,
)
from backend.app.services.ad_performance_analysis_service import AdPerformanceAnalysisService
from backend.app.services.collaboration import (
    OperatorContext,
    record_can_edit,
    require_read_access,
)
from backend.app.services.external_ad_performance_analysis_service import (
    AdAnalysisIdempotencyConflict,
    ExternalAdPerformanceAnalysisService,
)
from backend.app.services.generation_task_dispatcher import schedule_generation_task


class AdPerformanceRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        original_route_handler = super().get_route_handler()

        async def custom_route_handler(request: Request) -> Response:
            try:
                return await original_route_handler(request)
            except RequestValidationError as exc:
                if "/integrations/ad-performance/analysis-jobs" not in request.url.path:
                    raise
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content=AdAnalysisEnvelope(
                        code=AD_ANALYSIS_CODE_VALIDATION_ERROR,
                        message="request validation failed",
                        data={"errors": jsonable_encoder(exc.errors())},
                    ).model_dump(mode="json"),
                )

        return custom_route_handler


router = APIRouter(route_class=AdPerformanceRoute)
service = AdPerformanceAnalysisService()
external_analysis_service = ExternalAdPerformanceAnalysisService()


@router.post(
    "/integrations/ad-performance/analysis-jobs",
    response_model=AdAnalysisEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_external_ad_performance_analysis_job(
    payload: ExternalAdPerformanceAnalysisCreate,
    session: DbSession,
    background_tasks: BackgroundTasks,
    request: Request,
):
    try:
        result = await external_analysis_service.create_analysis_job(session, payload)
    except AdAnalysisIdempotencyConflict as exc:
        return _ad_analysis_error_response(exc, status.HTTP_409_CONFLICT)
    except AppError as exc:
        return _ad_analysis_error_response(exc, status.HTTP_400_BAD_REQUEST)

    if result.task is not None:
        try:
            scheduled = schedule_generation_task(result.task, background_tasks)
            if not scheduled:
                raise RuntimeError("analysis task dispatch was not accepted")
        except Exception as exc:  # noqa: BLE001 - persist broker failure for idempotent retry.
            await external_analysis_service.record_dispatch_failure(
                session,
                result.analysis,
                exc,
            )
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content=AdAnalysisEnvelope(
                    code=AD_ANALYSIS_CODE_SERVER_ERROR,
                    message="analysis task dispatch failed; retry the same external_request_id",
                    data={
                        "analysis_id": str(
                            result.analysis.analysis_id or result.analysis.id
                        ),
                        "external_request_id": str(
                            result.analysis.external_request_id or ""
                        ),
                    },
                ).model_dump(mode="json"),
            )
        await external_analysis_service.record_dispatch_success(
            session,
            result.analysis,
        )
    http_status = status.HTTP_200_OK if result.idempotent_replay else status.HTTP_202_ACCEPTED
    code = AD_ANALYSIS_CODE_SUCCESS if result.idempotent_replay else AD_ANALYSIS_CODE_ACCEPTED
    message = "ok" if result.idempotent_replay else "accepted"
    return JSONResponse(
        status_code=http_status,
        content=AdAnalysisEnvelope(
            code=code,
            message=message,
            data=_analysis_create_data(
                result.analysis,
                request=request,
                idempotent_replay=result.idempotent_replay,
            ),
        ).model_dump(mode="json"),
    )


@router.get(
    "/integrations/ad-performance/analysis-jobs/{analysis_id}",
    response_model=AdAnalysisEnvelope,
)
async def get_external_ad_performance_analysis_job(
    analysis_id: str,
    session: DbSession,
):
    try:
        analysis = await external_analysis_service.get_analysis_job(session, analysis_id)
    except NotFoundError as exc:
        return _ad_analysis_error_response(exc, status.HTTP_404_NOT_FOUND)
    except AppError as exc:
        return _ad_analysis_error_response(exc, status.HTTP_400_BAD_REQUEST)

    return AdAnalysisEnvelope(
        code=AD_ANALYSIS_CODE_SUCCESS,
        message="ok",
        data=_analysis_job_data(analysis),
    )


@router.post(
    "/integrations/ad-performance/analyses",
    response_model=AdPerformanceAnalysisRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_ad_performance_analysis(
    payload: AdPerformanceAnalysisCreate,
    session: DbSession,
    operator: OptionalOperator,
):
    return _analysis_read(
        await service.create_analysis(session, payload, operator=operator),
        operator,
    )


@router.get(
    "/integrations/ad-performance/analyses",
    response_model=list[AdPerformanceAnalysisRead],
)
async def list_ad_performance_analyses(
    session: DbSession,
    operator: CurrentOperator,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    creative_external_id: str | None = Query(default=None),
):
    analyses = await service.list_analyses(
        session,
        limit=limit,
        offset=offset,
        creative_external_id=creative_external_id,
        operator=operator,
    )
    return [_analysis_read(analysis, operator) for analysis in analyses]


@router.get(
    "/integrations/ad-performance/analyses/{analysis_id}",
    response_model=AdPerformanceAnalysisRead,
)
async def get_ad_performance_analysis(
    analysis_id: str,
    session: DbSession,
    operator: CurrentOperator,
):
    analysis = await service.get_analysis(session, analysis_id)
    require_read_access(analysis, operator)
    return _analysis_read(analysis, operator)


@router.delete(
    "/integrations/ad-performance/analyses/{analysis_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_ad_performance_analysis(
    analysis_id: str,
    session: DbSession,
    operator: CurrentOperator,
):
    await service.delete_analysis(session, analysis_id, operator=operator)


@router.post(
    "/integrations/ad-performance/analyses/{analysis_id}/claim",
    response_model=AdPerformanceAnalysisRead,
)
async def claim_ad_performance_analysis(
    analysis_id: str,
    session: DbSession,
    operator: CurrentOperator,
):
    return _analysis_read(
        await service.claim_analysis(session, analysis_id, operator),
        operator,
    )


@router.post("/integrations/ad-performance/analyses/{analysis_id}/ai-analysis/stream")
async def stream_ad_performance_ai_analysis(
    analysis_id: str,
    session: DbSession,
    operator: CurrentOperator,
):
    async def event_stream():
        async for event in service.stream_ai_analysis(
            session,
            analysis_id,
            operator=operator,
        ):
            yield _sse_event(event.get("type", "message"), event)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _analysis_read(
    analysis: AdPerformanceAnalysis,
    operator: OperatorContext | None = None,
) -> AdPerformanceAnalysisRead:
    return AdPerformanceAnalysisRead(
        id=analysis.id,
        analysis_id=analysis.id,
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
        external_user_id=analysis.external_user_id,
        source_type=analysis.source_type,
        status=analysis.status,
        campaign_external_id=analysis.campaign_external_id,
        campaign_name=analysis.campaign_name,
        adset_external_id=analysis.adset_external_id,
        adset_name=analysis.adset_name,
        creative_external_id=analysis.creative_external_id,
        creative_name=analysis.creative_name,
        date_start=analysis.date_start,
        date_stop=analysis.date_stop,
        request_payload=analysis.request_payload or {},
        metrics=analysis.metrics or {},
        analysis_result=analysis.analysis_result or {},
        error_message=analysis.error_message,
        owner_user_id=analysis.owner_user_id,
        locked_by=analysis.locked_by,
        locked_at=analysis.locked_at,
        can_edit=record_can_edit(analysis, operator),
    )


def _analysis_create_data(
    analysis: AdPerformanceAnalysis,
    *,
    request: Request,
    idempotent_replay: bool,
) -> dict:
    return AdAnalysisCreateData(
        analysis_id=str(analysis.analysis_id or analysis.id),
        external_request_id=str(analysis.external_request_id or ""),
        status=_external_status(analysis.status),
        stage=str(analysis.stage or analysis.status or "queued"),
        created_at=_iso(analysis.created_at),
        poll_url=_poll_url(request, str(analysis.analysis_id or analysis.id)),
        idempotent_replay=idempotent_replay,
    ).model_dump(mode="json")


def _analysis_job_data(analysis: AdPerformanceAnalysis) -> dict:
    error = None
    if analysis.status == "failed":
        error = AdAnalysisJobError(
            error_code=analysis.error_code or "ad_analysis_failed",
            message=analysis.error_message or "Analysis failed.",
            retryable=bool(analysis.error_retryable),
        )
    return AdAnalysisJobData(
        analysis_id=str(analysis.analysis_id or analysis.id),
        external_request_id=str(analysis.external_request_id or ""),
        status=_external_status(analysis.status),
        stage=str(analysis.stage or analysis.status or "queued"),
        progress=max(min(int(analysis.progress or 0), 100), 0),
        created_at=_iso(analysis.created_at),
        started_at=_iso_optional(analysis.started_at),
        completed_at=_iso_optional(analysis.completed_at),
        result=analysis.analysis_result if analysis.status == "succeeded" else None,
        error=error,
    ).model_dump(mode="json")


def _ad_analysis_error_response(exc: Exception, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status,
        content=AdAnalysisEnvelope(
            code=AD_ANALYSIS_CODE_VALIDATION_ERROR,
            message=str(exc) or exc.__class__.__name__,
            data={},
        ).model_dump(mode="json"),
    )


def _external_status(value: str | None) -> str:
    if value in {"queued", "processing", "succeeded", "failed"}:
        return value
    if value in {"completed", "success"}:
        return "succeeded"
    if value in {"running"}:
        return "processing"
    return "queued"


def _poll_url(request: Request, analysis_id: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/v1/integrations/ad-performance/analysis-jobs/{analysis_id}"


def _iso(value) -> str:
    return _iso_optional(value) or ""


def _iso_optional(value) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
