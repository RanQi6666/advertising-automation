from pydantic import BaseModel, Field

from backend.app.schemas.common import TimestampedRead


class TopicGenerateRequest(BaseModel):
    campaign_id: str
    limit: int = Field(default=3, ge=1, le=3)
    signals: dict = Field(default_factory=dict)
    model_id: str | None = Field(default=None, max_length=128)


class TopicRead(TimestampedRead):
    campaign_id: str
    title: str
    angle: str
    audience: str | None = None
    selling_points: list[str] = Field(default_factory=list)
    risk_notes: str | None = None
    rationale: str | None = None
    status: str
    score: float | None = None
    source_data: dict = Field(default_factory=dict)
