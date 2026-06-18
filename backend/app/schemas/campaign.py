from pydantic import BaseModel, Field

from backend.app.schemas.common import TimestampedRead


class ClientCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    industry: str | None = None
    notes: str | None = None
    metadata_json: dict = Field(default_factory=dict)


class ClientRead(TimestampedRead):
    name: str
    industry: str | None = None
    notes: str | None = None
    metadata_json: dict = Field(default_factory=dict)


class BrandCreate(BaseModel):
    client_id: str
    name: str = Field(min_length=1, max_length=255)
    voice: str | None = None
    guidelines: str | None = None
    banned_words: list[str] = Field(default_factory=list)
    compliance_notes: str | None = None
    metadata_json: dict = Field(default_factory=dict)


class BrandRead(TimestampedRead):
    client_id: str
    name: str
    voice: str | None = None
    guidelines: str | None = None
    banned_words: list[str] = Field(default_factory=list)
    compliance_notes: str | None = None
    metadata_json: dict = Field(default_factory=dict)


class CampaignCreate(BaseModel):
    client_id: str | None = None
    brand_id: str | None = None
    work_order_id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    objective: str | None = None
    product_name: str | None = None
    audience_description: str | None = None
    budget_notes: str | None = None
    metadata_json: dict = Field(default_factory=dict)


class CampaignRead(TimestampedRead):
    client_id: str | None = None
    brand_id: str | None = None
    work_order_id: str | None = None
    name: str
    objective: str | None = None
    product_name: str | None = None
    audience_description: str | None = None
    budget_notes: str | None = None
    status: str
    metadata_json: dict = Field(default_factory=dict)
