from fastapi import APIRouter

from backend.app.schemas.model_options import ModelOptionsRead
from backend.app.services.model_selection import get_model_options

router = APIRouter(prefix="/model-options")


@router.get("", response_model=ModelOptionsRead)
async def read_model_options() -> ModelOptionsRead:
    return get_model_options()
