from typing import Literal

from pydantic import BaseModel, Field

from backend.app.schemas.common import TimestampedRead


class CreativeGenerateRequest(BaseModel):
    draft_id: str
    count: int = Field(default=3, ge=1, le=6)
    size: str = "1:1"
    target_index: int | None = Field(default=None, ge=1, le=6)
    model_id: str | None = Field(default=None, max_length=128)
    storyboard: list[dict] = Field(default_factory=list)
    storyboard_text: str | None = None
    generation_mode: Literal["standard", "video_keyframe_variants"] = "standard"
    variant_count: int = Field(default=3, ge=1, le=3)
    frames_per_variant: int = Field(default=2, ge=1, le=2)
    video_duration_seconds: int | None = Field(default=None, ge=1, le=300)


class CreativeRegenerateRequest(BaseModel):
    feedback: str = Field(min_length=1, max_length=2000)
    size: str | None = None
    model_id: str | None = Field(default=None, max_length=128)


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
