import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.session import get_session

DbSession = Annotated[AsyncSession, Depends(get_session)]


def require_ai_ads_access_token(
    authorization: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Query()] = None,
    ai_access_token: Annotated[str | None, Query()] = None,
) -> None:
    expected_token = get_settings().ai_ads_access_token
    if not expected_token:
        return

    provided_token = _bearer_token(authorization) or access_token or ai_access_token
    if provided_token and secrets.compare_digest(provided_token, expected_token):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing access token.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_material_generation_access_token(
    authorization: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Query()] = None,
    ai_access_token: Annotated[str | None, Query()] = None,
) -> None:
    expected_token = get_settings().ai_ads_access_token
    if not expected_token:
        return

    provided_token = _bearer_token(authorization) or access_token or ai_access_token
    if provided_token and secrets.compare_digest(provided_token, expected_token):
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={
            "code": 4003,
            "message": "invalid or missing access token",
            "data": {},
        },
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()
