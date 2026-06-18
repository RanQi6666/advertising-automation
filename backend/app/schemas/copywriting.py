from pydantic import BaseModel, Field

from backend.app.schemas.common import TimestampedRead


class CopyGenerateRequest(BaseModel):
    topic_id: str
    constraints: dict = Field(default_factory=dict)


class CopyReviseRequest(BaseModel):
    feedback: str = Field(min_length=1)
    constraints: dict = Field(default_factory=dict)


class CopyDraftRead(TimestampedRead):
    campaign_id: str
    topic_id: str
    body: str
    primary_text: str | None = None
    headline: str | None = None
    description: str | None = None
    cta: str | None = None
    status: str
    version: int
    model_name: str | None = None
    prompt_version: str | None = None
    metadata_json: dict = Field(default_factory=dict)
