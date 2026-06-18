from backend.app.db.models import Base
from backend.app.db.session import engine


async def create_all_tables() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
