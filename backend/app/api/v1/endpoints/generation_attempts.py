from fastapi import APIRouter

from backend.app.api.deps import DbSession
from backend.app.schemas.generation_attempt import GenerationAttemptRead
from backend.app.services.generation_attempt_service import GenerationAttemptService

router = APIRouter()
service = GenerationAttemptService()


@router.get("/generation-attempts/{attempt_id}", response_model=GenerationAttemptRead)
async def get_generation_attempt(attempt_id: str, session: DbSession):
    attempt = await service.get_attempt(session, attempt_id)
    return GenerationAttemptRead.from_model(attempt)
