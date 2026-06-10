from fastapi import APIRouter, Query, status

from backend.app.api.deps import DbSession
from backend.app.schemas.copywriting import CopyDraftRead, CopyGenerateRequest, CopyReviseRequest
from backend.app.services.copywriting_service import CopywritingService

router = APIRouter()
service = CopywritingService()


@router.post(
    "/copywriting/generate", response_model=CopyDraftRead, status_code=status.HTTP_201_CREATED
)
async def generate_copy(payload: CopyGenerateRequest, session: DbSession):
    return await service.generate_copy(session, payload)


@router.post("/copywriting/{draft_id}/revise", response_model=CopyDraftRead)
async def revise_copy(draft_id: str, payload: CopyReviseRequest, session: DbSession):
    return await service.revise_copy(session, draft_id, payload)


@router.get("/campaigns/{campaign_id}/drafts", response_model=list[CopyDraftRead])
async def list_drafts(
    campaign_id: str,
    session: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_drafts(session, campaign_id=campaign_id, limit=limit, offset=offset)
