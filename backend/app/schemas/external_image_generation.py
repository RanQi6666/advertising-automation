from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

IMAGE_GENERATION_CODE_SUCCESS = 0
IMAGE_GENERATION_CODE_PROCESSING = 1001
IMAGE_GENERATION_CODE_VALIDATION_ERROR = 4001
IMAGE_GENERATION_CODE_PROVIDER_ERROR = 5001


class ExternalImageGenerationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_request_id: str | None = Field(default=None, max_length=128)
    prompt: str = Field(min_length=1)
    count: int = Field(default=1, ge=1, le=5)
    size: str = "1:1"
    model_id: str | None = Field(default=None, max_length=128)


class ExternalImageRevisionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_request_id: str | None = Field(default=None, max_length=128)
    feedback: str = Field(min_length=1, max_length=2000)
    source_image_index: int = Field(default=1, ge=1)
    count: int | None = Field(default=None, ge=1, le=5)
    size: str | None = None
    model_id: str | None = Field(default=None, max_length=128)


class ExternalGeneratedImageRead(BaseModel):
    index: int
    url: str
    prompt: str


class ExternalImageGenerationJobRead(BaseModel):
    job_id: str
    status: Literal["processing", "succeeded", "failed"]
    images: list[ExternalGeneratedImageRead] = Field(default_factory=list)
    error_message: str | None = None
    count: int
    size: str
    model_id: str | None = None
    source_job_id: str | None = None
    mode: Literal["edit", "generate", "from_image"] | None = None


class ExternalImageGenerationEnvelope(BaseModel):
    code: int
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
