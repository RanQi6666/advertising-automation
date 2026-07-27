from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AI_GENERATION_CODE_SUCCESS = 0
AI_GENERATION_CODE_PROCESSING = 1001
AI_GENERATION_CODE_VALIDATION_ERROR = 4001
AI_GENERATION_CODE_PROVIDER_ERROR = 5001

ExternalAIWorkOrderType = Literal["gambling", "game", "ecommerce", "weight_loss"]


class ExternalAIGenerationEnvelope(BaseModel):
    code: int
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


class ExternalAIRequestBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_request_id: str | None = Field(default=None, max_length=128)
    language: str | None = Field(default=None, max_length=32)


class ExternalAIWorkOrderAnalysisCreate(ExternalAIRequestBase):
    work_order_type: ExternalAIWorkOrderType
    work_order_text: str = Field(min_length=1)
    media: str = Field(min_length=1, max_length=32)


class ExternalAITopicSelectionCreate(ExternalAIRequestBase):
    product_name: str = Field(min_length=1, max_length=255)
    brief: str | None = None
    country: str | None = Field(default=None, max_length=128)
    work_order_type: ExternalAIWorkOrderType | None = None
    industry: str | None = Field(default=None, max_length=128)
    count: int = Field(default=3, ge=1, le=5)
    campaign: dict[str, Any] = Field(default_factory=dict)
    adset: dict[str, Any] = Field(default_factory=dict)
    creative: dict[str, Any] = Field(default_factory=dict)


class ExternalAICopyGenerationCreate(ExternalAIRequestBase):
    product_name: str = Field(min_length=1, max_length=255)
    landing_url: str | None = None
    audience: str | None = None
    country: str | None = Field(default=None, max_length=128)
    event_name: str | None = Field(default=None, max_length=128)
    customEventType: str | None = Field(default=None, max_length=128)
    brief: str | None = None
    selling_points: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    campaign: dict[str, Any] = Field(default_factory=dict)
    adset: dict[str, Any] = Field(default_factory=dict)
    creative: dict[str, Any] = Field(default_factory=dict)
    count: int = Field(default=1, ge=1, le=5)


class ExternalAIVideoStoryboardCreate(ExternalAIRequestBase):
    product_name: str = Field(min_length=1, max_length=255)
    brief: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    duration_seconds: int = Field(default=12, ge=1, le=300)
    aspect_ratio: str = Field(default="9:16", max_length=32)
    prompt: str | None = None


class ExternalAIReferenceVideoURL(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["url"]
    video_url: str = Field(min_length=1, max_length=2048)


class ExternalAIReferenceVideoUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["uploaded_asset"]
    upload_asset_id: str = Field(min_length=1, max_length=255)


class ExternalAIReferenceVideoAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: Literal["video_asset"]
    video_asset_id: str = Field(min_length=1, max_length=255)


ExternalAIReferenceVideo = Annotated[
    ExternalAIReferenceVideoURL
    | ExternalAIReferenceVideoUpload
    | ExternalAIReferenceVideoAsset,
    Field(discriminator="source_type"),
]


class ExternalAIFrameAnchoredStoryboardCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_request_id: str | None = Field(default=None, max_length=128)
    first_frame_image_url: str = Field(min_length=1, max_length=10_000_000)
    last_frame_image_url: str = Field(min_length=1, max_length=10_000_000)
    reference_video: ExternalAIReferenceVideo | None = None
    duration_seconds: int = Field(default=12, ge=1, le=300)
    aspect_ratio: str = Field(default="9:16", max_length=32)
