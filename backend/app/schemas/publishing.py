from datetime import datetime

from pydantic import BaseModel, Field

from backend.app.db.models.enums import PublishChannel
from backend.app.schemas.common import TimestampedRead


class PublishJobCreate(BaseModel):
    campaign_id: str
    draft_id: str | None = None
    channel: PublishChannel
    payload: dict = Field(default_factory=dict)
    scheduled_for: datetime | None = None


class PublishJobRead(TimestampedRead):
    campaign_id: str
    draft_id: str | None = None
    channel: str
    payload: dict = Field(default_factory=dict)
    status: str
    external_id: str | None = None
    error_message: str | None = None
    scheduled_for: datetime | None = None
    published_at: datetime | None = None
    metadata_json: dict = Field(default_factory=dict)
