from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import NotFoundError
from backend.app.db.base import Base


async def get_required(session: AsyncSession, model: type[Base], object_id: str) -> Base:
    item = await session.get(model, object_id)
    if item is None:
        raise NotFoundError(f"{model.__name__} not found: {object_id}")
    return item
