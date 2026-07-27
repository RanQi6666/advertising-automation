from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.app.schemas.ai import ImageBrief
from backend.app.schemas.common import TimestampedRead


class CreativeGenerateRequest(BaseModel):
    draft_id: str
    count: int = Field(default=3, ge=1, le=6)
    size: str = "9:16"
    target_index: int | None = Field(default=None, ge=1, le=6)
    target_indices: list[int] = Field(default_factory=list, max_length=6)
    model_id: str | None = Field(default=None, max_length=128)
    storyboard: list[dict] = Field(default_factory=list)
    storyboard_text: str | None = None
    generation_mode: Literal["standard", "video_keyframe_variants"] = "standard"
    variant_count: int = Field(default=3, ge=1, le=3)
    frames_per_variant: int = Field(default=2, ge=1, le=2)
    video_duration_seconds: int | None = Field(default=None, ge=1, le=300)
    prepared_briefs: list[ImageBrief] = Field(default_factory=list, max_length=6)

    @field_validator("target_indices")
    @classmethod
    def validate_target_indices(cls, value: list[int]) -> list[int]:
        cleaned: list[int] = []
        for raw_index in value:
            index = int(raw_index)
            if index < 1 or index > 6:
                raise ValueError("target_indices entries must be between 1 and 6.")
            if index not in cleaned:
                cleaned.append(index)
        return cleaned

    @model_validator(mode="after")
    def validate_single_target_mode(self) -> "CreativeGenerateRequest":
        if self.target_index is not None and self.target_indices:
            raise ValueError("Use either target_index or target_indices, not both.")
        return self


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
