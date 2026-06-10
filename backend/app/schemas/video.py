from pydantic import BaseModel, Field

from backend.app.schemas.common import TimestampedRead


class VideoGenerateRequest(BaseModel):
    campaign_id: str
    creative_asset_ids: list[str] = Field(min_length=1, max_length=20)
    draft_id: str | None = None
    prompt: str | None = None
    duration_seconds: int | None = Field(default=None, ge=1, le=300)
    aspect_ratio: str = "9:16"
    metadata_json: dict = Field(default_factory=dict)


class VideoAssetRead(TimestampedRead):
    campaign_id: str
    draft_id: str | None = None
    source_asset_ids: list[str] = Field(default_factory=list)
    url: str | None = None
    storage_key: str | None = None
    prompt: str | None = None
    storyboard: list = Field(default_factory=list)
    duration_seconds: int | None = None
    aspect_ratio: str
    status: str
    provider_job_id: str | None = None
    error_message: str | None = None
    version: int
    metadata_json: dict = Field(default_factory=dict)
