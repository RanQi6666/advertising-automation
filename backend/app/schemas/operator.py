from datetime import datetime

from backend.app.schemas.common import TimestampedRead


class OperatorRead(TimestampedRead):
    email: str
    full_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
