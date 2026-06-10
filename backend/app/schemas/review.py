from pydantic import BaseModel, Field

from backend.app.db.models.enums import ReviewDecision, ReviewEntityType
from backend.app.schemas.common import TimestampedRead


class ReviewCreate(BaseModel):
    entity_type: ReviewEntityType
    entity_id: str
    campaign_id: str | None = None
    reviewer_id: str | None = None
    decision: ReviewDecision
    feedback: str | None = None
    metadata_json: dict = Field(default_factory=dict)


class ReviewTaskRead(TimestampedRead):
    campaign_id: str | None = None
    entity_type: str
    entity_id: str
    reviewer_id: str | None = None
    status: str
    decision: str | None = None
    feedback: str | None = None
    metadata_json: dict = Field(default_factory=dict)
