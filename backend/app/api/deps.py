from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.session import get_session

DbSession = Annotated[AsyncSession, Depends(get_session)]
