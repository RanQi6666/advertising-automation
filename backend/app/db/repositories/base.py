from typing import Generic, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import NotFoundError
from backend.app.db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    def __init__(self, session: AsyncSession, model: type[ModelT]) -> None:
        self.session = session
        self.model = model

    async def get(self, object_id: str) -> ModelT | None:
        return await self.session.get(self.model, object_id)

    async def get_required(self, object_id: str) -> ModelT:
        item = await self.get(object_id)
        if item is None:
            raise NotFoundError(f"{self.model.__name__} not found: {object_id}")
        return item

    def query(self) -> Select[tuple[ModelT]]:
        return select(self.model)
