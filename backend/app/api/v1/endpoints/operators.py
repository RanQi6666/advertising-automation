from fastapi import APIRouter
from sqlalchemy import select

from backend.app.api.deps import DbSession
from backend.app.db.base import utcnow
from backend.app.db.models.user import User
from backend.app.schemas.operator import OperatorRead

router = APIRouter()

DEFAULT_OPERATORS = (
    (
        "00000000-0000-4000-8000-000000000001",
        "operator001@local.ai",
        "操作员001",
        "operator",
    ),
    (
        "00000000-0000-4000-8000-000000000002",
        "operator002@local.ai",
        "操作员002",
        "operator",
    ),
    (
        "00000000-0000-4000-8000-000000000099",
        "admin@local.ai",
        "管理员",
        "admin",
    ),
)


@router.get("/operators", response_model=list[OperatorRead])
async def list_operators(session: DbSession):
    users = await _active_users(session)
    if not users:
        now = utcnow()
        session.add_all(
            [
                User(
                    id=user_id,
                    email=email,
                    full_name=full_name,
                    role=role,
                    is_active=True,
                    created_at=now,
                    updated_at=now,
                )
                for user_id, email, full_name, role in DEFAULT_OPERATORS
            ]
        )
        await session.commit()
        users = await _active_users(session)
    return sorted(users, key=_operator_sort_key)


async def _active_users(session: DbSession) -> list[User]:
    result = await session.execute(select(User).where(User.is_active.is_(True)))
    return list(result.scalars().all())


def _operator_sort_key(user: User) -> tuple[int, str]:
    role_order = 1 if user.role == "admin" else 0
    return role_order, user.full_name or user.email
