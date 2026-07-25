from typing import Any, Literal

from fastapi import status
from pydantic import BaseModel, Field, HttpUrl

MATERIAL_CODE_SUCCESS = 0
MATERIAL_CODE_PROCESSING = 1001
MATERIAL_CODE_VALIDATION_ERROR = 4001
MATERIAL_CODE_AUTH_ERROR = 4003
MATERIAL_CODE_PROVIDER_ERROR = 5001

MaterialJobStatus = Literal["processing", "succeeded", "failed"]


class MaterialGenerationBaseRequest(BaseModel):
    external_request_id: str | None = Field(default=None, max_length=128)
    product_name: str | None = Field(default=None, max_length=255)
    landing_url: HttpUrl | None = None
    audience: str | None = None
    country: str | None = None
    event_name: str | None = None
    customEventType: str | None = None
    language: str | None = None
    brief: str | None = None
    selling_points: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)


class MaterialCopyGenerateRequest(MaterialGenerationBaseRequest):
    pass


class MaterialImageGenerateRequest(MaterialGenerationBaseRequest):
    count: int = Field(default=1, ge=1, le=5)
    size: str = "9:16"


class MaterialVideoGenerateRequest(MaterialGenerationBaseRequest):
    image_urls: list[HttpUrl] = Field(default_factory=list)
    duration_seconds: int = Field(default=6, ge=1, le=300)
    aspect_ratio: str = "9:16"
    prompt: str | None = None


class MaterialGenerationEnvelope(BaseModel):
    code: int
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class MaterialGenerationAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: int = MATERIAL_CODE_VALIDATION_ERROR,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.code = code
        self.status_code = status_code
        self.data = data or {}
        super().__init__(message)
