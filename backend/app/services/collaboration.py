from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import or_
from sqlalchemy.sql import Select

from backend.app.db.base import utcnow

LOCK_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class OperatorContext:
    id: str
    email: str
    full_name: str | None
    role: str


def is_admin(operator: OperatorContext | None) -> bool:
    return operator is not None and operator.role == "admin"


def filter_for_operator(
    statement: Select[Any],
    model: type[Any],
    operator: OperatorContext,
) -> Select[Any]:
    if is_admin(operator):
        return statement
    return statement.where(or_(model.owner_user_id.is_(None), model.owner_user_id == operator.id))


def require_read_access(record: Any, operator: OperatorContext) -> None:
    if _operator_can_access(record, operator):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This item is assigned to another operator.",
    )


def require_write_access(record: Any, operator: OperatorContext) -> None:
    owner_user_id = getattr(record, "owner_user_id", None)
    if is_admin(operator):
        renew_lock(record, operator)
        return
    if owner_user_id in (None, operator.id):
        if owner_user_id is None:
            record.owner_user_id = operator.id
        renew_lock(record, operator)
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="This item is assigned to another operator.",
    )


def claim_record(record: Any, operator: OperatorContext) -> Any:
    owner_user_id = getattr(record, "owner_user_id", None)
    if is_admin(operator):
        if owner_user_id is None:
            record.owner_user_id = operator.id
        renew_lock(record, operator)
        return record
    if owner_user_id in (None, operator.id) or lock_is_expired(getattr(record, "locked_at", None)):
        record.owner_user_id = operator.id
        renew_lock(record, operator)
        return record
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="This item is currently being handled by another operator.",
    )


def renew_lock(record: Any, operator: OperatorContext) -> None:
    record.locked_by = operator.id
    record.locked_at = utcnow()


def record_can_edit(record: Any, operator: OperatorContext | None) -> bool:
    if operator is None:
        return False
    owner_user_id = getattr(record, "owner_user_id", None)
    return is_admin(operator) or owner_user_id in (None, operator.id)


def lock_is_expired(locked_at: datetime | None) -> bool:
    if locked_at is None:
        return True
    return _aware_datetime(locked_at) + LOCK_TTL <= utcnow()


def require_fresh_timestamp(record: Any, expected_updated_at: datetime | None) -> None:
    if expected_updated_at is None:
        return
    current_updated_at = getattr(record, "updated_at", None)
    if current_updated_at is None or not _same_timestamp(current_updated_at, expected_updated_at):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This item has been updated by someone else. Please refresh and try again.",
        )


def _operator_can_access(record: Any, operator: OperatorContext) -> bool:
    owner_user_id = getattr(record, "owner_user_id", None)
    return is_admin(operator) or owner_user_id in (None, operator.id)


def _same_timestamp(left: datetime, right: datetime) -> bool:
    return abs((_aware_datetime(left) - _aware_datetime(right)).total_seconds()) < 0.001


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
