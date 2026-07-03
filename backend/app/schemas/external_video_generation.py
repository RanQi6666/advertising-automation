from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

VIDEO_GENERATION_CODE_SUCCESS = 0
VIDEO_GENERATION_CODE_PROCESSING = 1001
VIDEO_GENERATION_CODE_VALIDATION_ERROR = 4001
VIDEO_GENERATION_CODE_PROVIDER_ERROR = 5001


class ExternalVideoGenerationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_request_id: str | None = Field(default=None, max_length=128)
    images: list[str] = Field(default_factory=list)
    storyboard_text: str = Field(min_length=1)
    duration_seconds: int = Field(default=12, ge=1, le=300)
    aspect_ratio: str = "9:16"


class ExternalVideoGenerationJobRead(BaseModel):
    job_id: str
    status: Literal["processing", "succeeded", "failed"]
    video_url: str | None = None
    error_message: str | None = None
    duration_seconds: int | None = None
    aspect_ratio: str


class ExternalVideoGenerationEnvelope(BaseModel):
    code: int
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
