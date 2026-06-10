from pydantic import BaseModel, Field

from backend.app.schemas.common import TimestampedRead


class CreativeGenerateRequest(BaseModel):
    draft_id: str
    count: int = Field(default=3, ge=1, le=5)
    size: str = "1:1"


class CreativeAssetRead(TimestampedRead):
    campaign_id: str
    draft_id: str
    kind: str
    url: str | None = None
    storage_key: str | None = None
    prompt: str
    alt_text: str | None = None
    size: str
    status: str
    version: int
    metadata_json: dict = Field(default_factory=dict)
