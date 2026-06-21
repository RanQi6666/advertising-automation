from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl

from backend.app.schemas.common import TimestampedRead
from backend.app.schemas.work_order import WorkOrderDeliveryExtractionRead

AdGenerationStatus = Literal[
    "queued",
    "processing",
    "fields_review",
    "topic_review",
    "copy_review",
    "image_review",
    "video_review",
    "final_review",
    "generated",
    "reviewing",
    "reviewed",
    "returned",
    "completed",
    "failed",
]
CreativeType = Literal["image", "video", "carousel"]


class PublishingWorkOrderPayload(BaseModel):
    raw_content: str = Field(min_length=1)
    structured_fields: dict = Field(default_factory=dict)
    delivery_extraction: WorkOrderDeliveryExtractionRead | None = None


class PublishingAdGenerationPreferences(BaseModel):
    creative_type: CreativeType = "image"
    image_count: int = Field(default=1, ge=1, le=5)
    daily_budget: int | None = Field(default=5000, ge=1)
    video_required: bool = False


class PublishingAdGenerationJobCreate(BaseModel):
    external_order_id: str | None = None
    callback_url: HttpUrl | None = None
    return_url: HttpUrl | None = None
    work_order: PublishingWorkOrderPayload
    preferences: PublishingAdGenerationPreferences = Field(
        default_factory=PublishingAdGenerationPreferences
    )
    metadata_json: dict = Field(default_factory=dict)


class PublishingAdGenerationJobAccepted(BaseModel):
    job_id: str
    status: AdGenerationStatus
    review_url: str


class PublishingAdGenerationReviewUpdate(BaseModel):
    result_payload: dict = Field(default_factory=dict)
    review_notes: str | None = None


class PublishingAdGenerationReviewConfirm(BaseModel):
    result_payload: dict | None = None
    review_notes: str | None = None


class PublishingCampaignPayload(BaseModel):
    name: str
    objective: str
    status: str = "PAUSED"
    draft: int = 1


class PublishingAdSetPayload(BaseModel):
    name: str
    daily_budget: int | None = None
    billing_event: str = "IMPRESSIONS"
    optimization_goal: str = "LINK_CLICKS"
    bid_strategy: str = "LOWEST_COST_WITHOUT_CAP"
    event_name: str | None = None
    countries: str
    country_code: str
    country_label: str | None = None
    age_min: int = 18
    age_max: int = 65
    gender: str | None = None
    audience_description: str | None = None
    start_type: str = "I"
    start_time: int = 0
    status: str = "PAUSED"
    draft: int = 1


class PublishingCreativePayload(BaseModel):
    name: str
    type: CreativeType = "image"
    message: str
    link: str | None = None
    ads_name: str | None = None
    description: str | None = None
    btn_type: str = "LEARN_MORE"
    asset_url: str | None = None
    asset_id: str | None = None
    image_asset_url: str | None = None
    video_asset_url: str | None = None
    material_url: str | None = None
    file_url: str | None = None
    image_url: str | None = None
    video_url: str | None = None
    draft: int = 1


class PublishingGeneratedImage(BaseModel):
    id: str | None = None
    filename: str | None = None
    url: str | None = None
    asset_url: str | None = None
    material_url: str | None = None
    file_url: str | None = None
    image_url: str | None = None
    image_asset_url: str | None = None
    type: str | None = None
    size: str | None = None
    prompt: str | None = None
    alt_text: str | None = None


class PublishingGeneratedVideo(BaseModel):
    id: str | None = None
    url: str | None = None
    asset_url: str | None = None
    material_url: str | None = None
    file_url: str | None = None
    video_url: str | None = None
    video_asset_url: str | None = None
    type: str | None = None
    cover_url: str | None = None
    duration_seconds: int | None = None
    storyboard: list[dict] = Field(default_factory=list)


class PublishingAdGenerationAssets(BaseModel):
    images: list[PublishingGeneratedImage] = Field(default_factory=list)
    videos: list[PublishingGeneratedVideo] = Field(default_factory=list)


class PublishingAdGenerationReview(BaseModel):
    missing_fields: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    low_confidence_fields: list[str] = Field(default_factory=list)


class PublishingAdGenerationResult(BaseModel):
    job_id: str
    external_order_id: str | None = None
    status: AdGenerationStatus
    campaign_payload: PublishingCampaignPayload | None = None
    adset_payload: PublishingAdSetPayload | None = None
    creative_payload: PublishingCreativePayload | None = None
    assets: PublishingAdGenerationAssets = Field(default_factory=PublishingAdGenerationAssets)
    review: PublishingAdGenerationReview = Field(default_factory=PublishingAdGenerationReview)
    metadata_json: dict = Field(default_factory=dict)


class PublishingAdGenerationJobRead(TimestampedRead):
    external_order_id: str | None = None
    status: AdGenerationStatus
    callback_url: str | None = None
    request_payload: dict = Field(default_factory=dict)
    result_payload: dict = Field(default_factory=dict)
    error_message: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata_json: dict = Field(default_factory=dict)
    review_url: str | None = None
    return_url: str | None = None
