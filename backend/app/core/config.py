from functools import lru_cache
from typing import Annotated, Any, Literal, Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    project_name: str = "Advertising Automation API"
    environment: str = "local"
    api_v1_prefix: str = "/api/v1"

    database_url: str = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5433/ad_automation"
    sync_database_url: str | None = None
    create_db_on_startup: bool = False
    db_pool_size: int = Field(default=5, ge=1, le=100)
    db_max_overflow: int = Field(default=5, ge=0, le=100)

    secret_key: str = "change-me-in-production"
    ai_ads_access_token: str | None = None
    ai_ads_return_url: str | None = None
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    llm_provider: Literal["mock", "openai", "volcengine", "gateway"] = "mock"
    llm_model: str = "gpt-4.1-mini"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    model_gateway_api_key: str | None = None
    model_gateway_base_url: str | None = None
    model_gateway_text_model: str | None = None
    model_gateway_text_timeout_seconds: float = Field(default=180.0, ge=1, le=600)
    model_gateway_text_fast_timeout_seconds: float = Field(default=45.0, ge=1, le=600)
    model_gateway_image_timeout_seconds: float = Field(default=300.0, ge=1, le=900)
    generation_task_execution_backend: Literal["background_tasks", "celery"] = (
        "background_tasks"
    )
    celery_broker_url: str = "redis://127.0.0.1:6379/0"
    celery_result_backend: str = "redis://127.0.0.1:6379/1"
    redis_url: str | None = None
    text_queue_concurrency: int = Field(default=6, ge=1, le=64)
    image_queue_concurrency: int = Field(default=4, ge=1, le=32)
    external_image_generation_max_attempts: int = Field(default=3, ge=1, le=5)
    external_image_route_mode: Literal["fixed", "round_robin"] = "fixed"
    external_image_route_providers: Annotated[
        list[Literal["gateway", "volcengine", "cpa_gemini"]], NoDecode
    ] = Field(default_factory=lambda: ["gateway", "volcengine"])
    video_queue_concurrency: int = Field(default=4, ge=1, le=16)
    callback_queue_concurrency: int = Field(default=3, ge=1, le=16)
    ad_analysis_queue_concurrency: int = Field(default=2, ge=1, le=16)
    ad_research_worker_concurrency: int = Field(default=2, ge=1, le=16)
    ad_research_model_concurrency: int = Field(default=6, ge=1, le=64)
    ad_research_media_concurrency: int = Field(default=6, ge=1, le=32)
    ad_research_frame_concurrency: int = Field(default=4, ge=1, le=16)
    ad_research_media_root: str = "ad-research"
    ad_research_media_download_timeout_seconds: float = Field(default=60.0, ge=5, le=180)
    ad_research_media_download_max_bytes: int = Field(default=80 * 1024 * 1024, ge=1024 * 1024)
    ad_research_media_retry_attempts: int = Field(default=2, ge=0, le=3)
    ad_research_ffprobe_timeout_seconds: float = Field(default=12.0, ge=1, le=60)
    ad_research_ffmpeg_frame_timeout_seconds: float = Field(default=15.0, ge=1, le=60)
    ad_research_model: str = "gpt-5.4-mini"
    ad_research_model_timeout_seconds: float = Field(default=45.0, ge=1, le=180)
    ad_research_model_lease_seconds: int = Field(default=90, ge=10, le=600)
    ad_research_collector_base_url: str = "http://meta_ads_collector:8090"
    ad_research_collector_timeout_seconds: float = Field(default=120.0, ge=5, le=600)
    generation_task_target_concurrent_users: int = Field(default=30, ge=1, le=1000)
    generation_runtime_monitor_timeout_seconds: float = Field(default=1.0, ge=0.1, le=10)
    generation_runtime_monitor_cache_seconds: float = Field(default=2.0, ge=0, le=30)
    generation_task_recovery_enabled: bool = True
    generation_task_recovery_interval_seconds: float = Field(default=60.0, ge=5, le=3600)
    generation_task_queued_stale_seconds: float = Field(default=60.0, ge=1, le=3600)
    generation_task_running_stale_seconds: float = Field(default=1800.0, ge=60, le=86400)
    generation_task_auto_retry_enabled: bool = True
    generation_task_auto_retry_delays_seconds: Annotated[list[int], NoDecode] = Field(
        default_factory=lambda: [10, 30, 60]
    )
    model_provider_text_concurrency: int = Field(default=6, ge=1, le=64)
    model_provider_image_concurrency: int = Field(default=6, ge=1, le=32)
    model_provider_video_concurrency: int = Field(default=1, ge=1, le=16)
    model_provider_ad_analysis_concurrency: int = Field(default=2, ge=1, le=16)
    llm_text_rpm_limit: int = Field(default=60, ge=1, le=100000)
    llm_text_max_inflight: int = Field(default=16, ge=1, le=1000)
    job_status_cache_ttl_seconds: int = Field(default=600, ge=10, le=86400)
    model_gateway_image_model: str | None = None
    model_gateway_gemini_image_model: str | None = None
    model_gateway_image_size: str = "1024x1024"
    model_gateway_image_response_format: str | None = None
    model_gateway_image_edit_enabled: bool = True
    model_gateway_image_edit_path: str = "/images/edits"
    model_gateway_image_edit_model: str | None = None
    model_gateway_image_extra_body: dict[str, Any] = Field(default_factory=dict)
    model_gateway_text_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    model_gateway_image_models: Annotated[list[str], NoDecode] = Field(default_factory=list)
    ad_performance_llm_timeout_seconds: float = Field(default=45.0, ge=1, le=180)
    ad_performance_video_input_fps: float = Field(default=1.0, ge=0.2, le=5.0)
    ark_api_key: str | None = None
    volcengine_api_key: str | None = None
    volcengine_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    volcengine_model: str = "doubao-seed-1-8-251228"
    volcengine_image_model: str = "doubao-seedream-4-5-251128"
    volcengine_image_size: str = "2K"
    volcengine_image_watermark: bool = False
    volcengine_video_api_key: str | None = None
    volcengine_video_model: str = "doubao-seedance-1-5-pro"
    volcengine_video_resolution: str = "480p"
    volcengine_video_image_mode: Literal["first_last_frame", "reference_images"] = (
        "first_last_frame"
    )
    volcengine_video_min_duration_seconds: int = Field(default=4, ge=1, le=300)
    volcengine_video_max_duration_seconds: int = Field(default=12, ge=1, le=300)
    volcengine_video_max_reference_images: int = Field(default=2, ge=1, le=20)
    volcengine_video_generate_audio: bool = True
    volcengine_video_watermark: bool = False
    volcengine_video_return_last_frame: bool = False
    volcengine_video_timeout_seconds: float = Field(default=60.0, ge=1, le=600)
    volcengine_video_execution_expires_after: int = Field(default=172800, ge=3600, le=259200)
    volcengine_video_priority: int = Field(default=0, ge=0, le=9)
    volcengine_video_safety_identifier: str | None = None

    image_provider: Literal["placeholder", "volcengine", "gateway", "cpa_gemini"] = "placeholder"
    video_provider: Literal["placeholder", "volcengine"] = "placeholder"
    object_storage_provider: Literal["local", "s3", "r2", "minio"] = "local"
    public_base_url: str = "http://127.0.0.1:8001"
    ad_generation_review_base_url: str = "http://127.0.0.1:5173"
    local_storage_root: str = "storage"
    image_download_timeout_seconds: float = 60.0
    image_download_max_bytes: int = 25 * 1024 * 1024
    video_download_timeout_seconds: float = 120.0
    video_download_max_bytes: int = 500 * 1024 * 1024
    storyboard_reference_video_max_duration_seconds: int = Field(default=30, ge=1, le=30)
    storyboard_reference_video_sample_interval_seconds: float = Field(
        default=2.0, ge=0.5, le=10
    )
    storyboard_reference_video_adaptive_sampling_enabled: bool = True
    storyboard_reference_video_adaptive_max_frames: int = Field(default=12, ge=2, le=24)
    storyboard_reference_video_scene_change_threshold: float = Field(
        default=0.18, ge=0.01, le=1.0
    )
    storyboard_reference_video_scene_change_max_candidates: int = Field(
        default=8, ge=0, le=20
    )
    storyboard_reference_video_frame_width: int = Field(default=768, ge=320, le=1920)
    storyboard_reference_video_jpeg_quality: int = Field(default=4, ge=2, le=31)
    storyboard_reference_video_ffprobe_timeout_seconds: float = Field(
        default=10.0, ge=0.01, le=120
    )
    storyboard_reference_video_ffmpeg_timeout_seconds: float = Field(
        default=15.0, ge=0.01, le=120
    )
    storyboard_reference_video_allow_private_hosts: bool = False
    ad_analysis_media_root: str = "data/ad-analysis-media"
    ad_analysis_media_processing_enabled: bool = True
    ad_analysis_allow_private_media_hosts: bool = False
    ad_analysis_media_download_timeout_seconds: float = Field(default=20.0, ge=1, le=120)
    ad_analysis_image_download_timeout_seconds: float = Field(default=30.0, ge=1, le=120)
    ad_analysis_video_download_timeout_seconds: float = Field(default=80.0, ge=1, le=180)
    ad_analysis_media_download_retry_attempts: int = Field(default=1, ge=0, le=3)
    ad_analysis_media_download_retry_delay_seconds: float = Field(default=1.0, ge=0, le=30)
    ad_analysis_ffprobe_timeout_seconds: float = Field(default=10.0, ge=0.01, le=120)
    ad_analysis_ffmpeg_frame_timeout_seconds: float = Field(default=15.0, ge=0.01, le=120)
    ad_analysis_image_download_max_bytes: int = Field(
        default=25 * 1024 * 1024, ge=1, le=100 * 1024 * 1024
    )
    ad_analysis_video_max_duration_seconds: int = Field(default=20, ge=1, le=120)
    ad_analysis_video_download_max_bytes: int = Field(
        default=100 * 1024 * 1024, ge=1, le=500 * 1024 * 1024
    )
    ad_analysis_public_research_enabled: bool = True
    public_research_enabled: bool = True
    ad_analysis_public_research_timeout_seconds: float = Field(default=6.0, ge=1, le=30)
    ad_analysis_public_research_max_results: int = Field(default=3, ge=1, le=10)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("external_image_route_providers", mode="before")
    @classmethod
    def parse_external_image_route_providers(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [provider.strip() for provider in value.split(",") if provider.strip()]
        return value

    @field_validator("model_gateway_text_models", "model_gateway_image_models", mode="before")
    @classmethod
    def parse_model_gateway_models(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [model.strip() for model in value.split(",") if model.strip()]
        return value

    @field_validator("generation_task_auto_retry_delays_seconds", mode="before")
    @classmethod
    def parse_auto_retry_delays(cls, value: str | list[int]) -> list[int]:
        if isinstance(value, str):
            return [
                int(part.strip())
                for part in value.split(",")
                if part.strip() and int(part.strip()) >= 0
            ]
        return value

    @model_validator(mode="after")
    def fill_redis_url(self) -> Self:
        if not self.redis_url:
            self.redis_url = self.celery_broker_url
        return self

@lru_cache
def get_settings() -> Settings:
    return Settings()
