from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl

from backend.app.schemas.common import TimestampedRead


class LandingPageAnalyzeRequest(BaseModel):
    url: HttpUrl | None = None
    force_refresh: bool = False
    metadata_json: dict = Field(default_factory=dict)


class LandingPageSnapshotRead(TimestampedRead):
    campaign_id: str
    work_order_id: str | None = None
    url: str
    status: str
    http_status: int | None = None
    title: str | None = None
    description: str | None = None
    text_content: str | None = None
    extracted_data: dict = Field(default_factory=dict)
    error_message: str | None = None
    fetched_at: datetime
    metadata_json: dict = Field(default_factory=dict)
