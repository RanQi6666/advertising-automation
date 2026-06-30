import json

from fastapi import APIRouter, Query, status
from starlette.responses import StreamingResponse

from backend.app.api.deps import CurrentOperator, DbSession, OptionalOperator
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.schemas.ad_performance import (
    AdPerformanceAnalysisCreate,
    AdPerformanceAnalysisRead,
)
from backend.app.services.ad_performance_analysis_service import AdPerformanceAnalysisService
from backend.app.services.collaboration import (
    OperatorContext,
    record_can_edit,
    require_read_access,
)

router = APIRouter()
service = AdPerformanceAnalysisService()


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


def _sse_event(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
