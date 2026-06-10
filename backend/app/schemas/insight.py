from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from backend.app.schemas.common import TimestampedRead


class InsightDailyCreate(BaseModel):
    campaign_id: str
    publish_job_id: str | None = None
    metric_date: date
    impressions: int = Field(default=0, ge=0)
    clicks: int = Field(default=0, ge=0)
    spend: Decimal = Field(default=Decimal("0"), ge=0)
    conversions: int = Field(default=0, ge=0)
    metadata_json: dict = Field(default_factory=dict)


class InsightDailyRead(TimestampedRead):
    campaign_id: str
    publish_job_id: str | None = None
    metric_date: date
    impressions: int
    clicks: int
    spend: Decimal
    ctr: Decimal | None = None
    cpc: Decimal | None = None
    conversions: int
    metadata_json: dict = Field(default_factory=dict)
