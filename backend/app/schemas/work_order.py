from typing import Any, Literal

from pydantic import BaseModel, Field

from backend.app.schemas.common import TimestampedRead

DeliveryFieldStatus = Literal["extracted", "suggested", "missing", "conflict"]


class WorkOrderDeliveryField(BaseModel):
    value: Any | None = None
    normalized_value: Any | None = None
    status: DeliveryFieldStatus = "missing"
    confidence: float = Field(default=0, ge=0, le=1)
    evidence: list[str] = Field(default_factory=list)
    candidates: list[Any] = Field(default_factory=list)
    reason: str | None = None


class WorkOrderDeliveryFields(BaseModel):
    landing_url: WorkOrderDeliveryField = Field(default_factory=WorkOrderDeliveryField)
    event_name: WorkOrderDeliveryField = Field(default_factory=WorkOrderDeliveryField)
    country: WorkOrderDeliveryField = Field(default_factory=WorkOrderDeliveryField)
    age_min: WorkOrderDeliveryField = Field(default_factory=WorkOrderDeliveryField)
    age_max: WorkOrderDeliveryField = Field(default_factory=WorkOrderDeliveryField)
    gender: WorkOrderDeliveryField = Field(default_factory=WorkOrderDeliveryField)
    audience_description_raw: WorkOrderDeliveryField = Field(
        default_factory=WorkOrderDeliveryField
    )


class WorkOrderDeliveryExtractionRequest(BaseModel):
    raw_content: str = Field(min_length=1)


class WorkOrderDeliveryExtractionRead(BaseModel):
    schema_version: str = "ad_delivery_extract_v1"
    fields: WorkOrderDeliveryFields = Field(default_factory=WorkOrderDeliveryFields)
    review: dict = Field(default_factory=dict)


class WorkOrderCreate(BaseModel):
    raw_content: str = Field(min_length=1)
    reviewed_delivery_fields: dict = Field(default_factory=dict)
    llm_delivery_fields: dict = Field(default_factory=dict)
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
