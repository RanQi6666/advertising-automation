from fastapi import APIRouter, Query, status

from backend.app.api.deps import DbSession
from backend.app.schemas.review import ReviewCreate, ReviewTaskRead
from backend.app.services.review_service import ReviewService

router = APIRouter()
service = ReviewService()


@router.post("/reviews", response_model=ReviewTaskRead, status_code=status.HTTP_201_CREATED)
async def submit_review(payload: ReviewCreate, session: DbSession):
    return await service.submit_review(session, payload)


@router.get("/reviews/pending", response_model=list[ReviewTaskRead])
async def list_pending_reviews(
    session: DbSession,
    campaign_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    return await service.list_pending(
        session,
        campaign_id=campaign_id,
        limit=limit,
        offset=offset,
    )
