from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from backend.app.db.models.enums import PublishChannel
from backend.app.schemas.common import TimestampedRead


class PublishJobCreate(BaseModel):
    campaign_id: str
    draft_id: str | None = None
    channel: PublishChannel
    payload: dict = Field(default_factory=dict)
    scheduled_for: datetime | None = None


class AdCreativeDraftRequest(BaseModel):
    campaign_id: str
    facebook_account_id: str | None = None
    draft_id: str | None = None
    topic_id: str | None = None
    creative_asset_id: str | None = None
    facebook_image_hash: str | None = None
    video_asset_id: str | None = None
    facebook_video_id: str | None = None
    page_id: str | None = None
    ad_account_id: str | None = None
    destination_url: str | None = None
    cta_type: str = "LEARN_MORE"


class AdCreativeDraftRead(BaseModel):
    campaign_id: str
    facebook_account_id: str | None = None
    draft_id: str | None = None
    topic_id: str | None = None
    destination_url: str | None = None
    headline: str
    primary_text: str
    description: str | None = None
    media_type: str
    creative_asset_id: str | None = None
    image_url: str | None = None
    facebook_image_hash: str | None = None
    video_asset_id: str | None = None
    facebook_video_id: str | None = None
    page_id: str | None = None
    ad_account_id: str | None = None
    cta_type: str
    meta_payload: dict = Field(default_factory=dict)
    source_mapping: dict = Field(default_factory=dict)


class AdsPlanDraftRequest(BaseModel):
    campaign_id: str
    facebook_account_id: str | None = None
    draft_id: str | None = None
    topic_id: str | None = None
    creative_asset_id: str | None = None
    facebook_image_hash: str | None = None
    video_asset_id: str | None = None
    facebook_video_id: str | None = None
    ad_creative_id: str | None = None
    page_id: str | None = None
    ad_account_id: str | None = None
    destination_url: str | None = None
    cta_type: str = "LEARN_MORE"
    daily_budget: int | None = Field(default=None, ge=1)
    pixel_id: str | None = None
    conversion_event: str | None = None
    campaign_name: str | None = None
    adset_name: str | None = None
    ad_name: str | None = None
    status: str = "PAUSED"


class AdsPlanDraftRead(BaseModel):
    campaign_id: str
    facebook_account_id: str | None = None
    draft_id: str | None = None
    topic_id: str | None = None
    destination_url: str | None = None
    headline: str
    primary_text: str
    media_type: str
    creative_asset_id: str | None = None
    facebook_image_hash: str | None = None
    page_id: str | None = None
    ad_account_id: str | None = None
    ad_creative_id: str | None = None
    campaign_payload: dict[str, Any] = Field(default_factory=dict)
    adset_payload: dict[str, Any] = Field(default_factory=dict)
    creative_payload: dict[str, Any] = Field(default_factory=dict)
    ad_payload: dict[str, Any] = Field(default_factory=dict)
    meta_payload: dict[str, Any] = Field(default_factory=dict)
    targeting_summary: dict[str, Any] = Field(default_factory=dict)
    source_mapping: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class MetaAdsDraftCreateRequest(BaseModel):
    campaign_id: str
    facebook_account_id: str | None = None
    draft_id: str
    topic_id: str | None = None
    creative_asset_id: str | None = None
    facebook_image_hash: str | None = None
    video_asset_id: str | None = None
    facebook_video_id: str | None = None
    page_id: str | None = None
    ad_account_id: str | None = None
    destination_url: str | None = None
    cta_type: str = "LEARN_MORE"
    daily_budget: int = Field(ge=1)
    pixel_id: str | None = None
    conversion_event: str | None = None
    campaign_name: str | None = None
    adset_name: str | None = None
    ad_name: str | None = None
    confirm_create_paused: bool = False


class MetaAdsPackagePrepareRequest(BaseModel):
    campaign_id: str
    facebook_account_id: str | None = None
    draft_id: str
    topic_id: str | None = None
    creative_asset_id: str | None = None
    facebook_image_hash: str | None = None
    video_asset_id: str | None = None
    facebook_video_id: str | None = None
    page_id: str | None = None
    ad_account_id: str | None = None
    destination_url: str | None = None
    cta_type: str = "LEARN_MORE"
    daily_budget: int = Field(ge=1)
    pixel_id: str | None = None
    conversion_event: str | None = None
    campaign_name: str | None = None
    adset_name: str | None = None
    ad_name: str | None = None
    confirm_prepare: bool = False


class MetaAdsDraftCreateRead(BaseModel):
    job_id: str
    status: str
    dry_run: bool
    campaign_id: str
    draft_id: str | None = None
    meta_campaign_id: str | None = None
    meta_adset_id: str | None = None
    meta_ad_creative_id: str | None = None
    meta_ad_id: str | None = None
    error_message: str | None = None
    ids: dict[str, Any] = Field(default_factory=dict)
    responses: dict[str, Any] = Field(default_factory=dict)
    plan: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class MetaAdsActivationRequest(BaseModel):
    confirm_activate: bool = False
    confirmation_text: str | None = None


class MetaAdsPauseRequest(BaseModel):
    confirm_pause: bool = False
    confirmation_text: str | None = None


class AdPixelRead(BaseModel):
    id: str
    name: str | None = None
    last_fired_time: str | None = None
    metadata_json: dict = Field(default_factory=dict)


class PublishJobRead(TimestampedRead):
    campaign_id: str
    draft_id: str | None = None
    channel: str
    payload: dict = Field(default_factory=dict)
    status: str
    external_id: str | None = None
    error_message: str | None = None
    scheduled_for: datetime | None = None
    published_at: datetime | None = None
    metadata_json: dict = Field(default_factory=dict)


class FacebookPagePublishConfig(BaseModel):
    id: str | None = None
    id_configured: bool
    access_token_configured: bool
    access_token_ref: str


class FacebookAdsPublishConfig(BaseModel):
    ad_account_id: str | None = None
    ad_account_configured: bool
    access_token_configured: bool
    access_token_ref: str
    dry_run: bool


class FacebookAppPublishConfig(BaseModel):
    app_id: str | None = None
    app_id_configured: bool
    app_secret_configured: bool


class FacebookPublishConfigRead(BaseModel):
    dry_run: bool
    graph_api_version: str
    app: FacebookAppPublishConfig
    page: FacebookPagePublishConfig
    ads: FacebookAdsPublishConfig
    missing_fields: list[str] = Field(default_factory=list)
