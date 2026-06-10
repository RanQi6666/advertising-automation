from pydantic import BaseModel, Field

from backend.app.schemas.common import TimestampedRead


class WorkOrderCreate(BaseModel):
    raw_content: str = Field(min_length=1)
    metadata_json: dict = Field(default_factory=dict)


class WorkOrderRead(TimestampedRead):
    raw_content: str
    parsed_fields: dict = Field(default_factory=dict)
    project_name: str | None = None
    country: str | None = None
    media: str | None = None
    event_name: str | None = None
    product_name: str | None = None
    audience_description: str | None = None
    landing_url: str | None = None
    report_timezone: str | None = None
    status: str
    metadata_json: dict = Field(default_factory=dict)


class CampaignFromWorkOrderRequest(BaseModel):
    client_id: str | None = None
    brand_id: str | None = None
    name: str | None = None
    objective: str | None = None
    product_name: str | None = None
    audience_description: str | None = None
    metadata_json: dict = Field(default_factory=dict)
