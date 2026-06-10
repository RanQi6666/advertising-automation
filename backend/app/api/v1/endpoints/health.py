from fastapi import APIRouter
from sqlalchemy import text

from backend.app.api.deps import DbSession

router = APIRouter()


@router.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ready")
async def ready(session: DbSession) -> dict[str, str]:
    await session.execute(text("select 1"))
    return {"status": "ready"}
