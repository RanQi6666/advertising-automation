import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.user import User
from backend.app.db.session import get_session
from backend.app.services.collaboration import OperatorContext

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


async def require_current_operator(
    session: DbSession,
    x_operator_id: Annotated[str | None, Header(alias="X-Operator-Id")] = None,
    operator_id: Annotated[str | None, Query()] = None,
) -> OperatorContext:
    operator = await get_optional_current_operator(
        session=session,
        x_operator_id=x_operator_id,
        operator_id=operator_id,
    )
    if operator is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing operator identity.",
        )
    return operator


async def get_optional_current_operator(
    session: DbSession,
    x_operator_id: Annotated[str | None, Header(alias="X-Operator-Id")] = None,
    operator_id: Annotated[str | None, Query()] = None,
) -> OperatorContext | None:
    raw_operator_id = (x_operator_id or operator_id or "").strip()
    if not raw_operator_id:
        return None

    user = await session.scalar(
        select(User).where(User.id == raw_operator_id, User.is_active.is_(True))
    )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid operator identity.",
        )
    return OperatorContext(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
    )


CurrentOperator = Annotated[OperatorContext, Depends(require_current_operator)]
OptionalOperator = Annotated[OperatorContext | None, Depends(get_optional_current_operator)]
