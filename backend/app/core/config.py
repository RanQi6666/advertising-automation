from functools import lru_cache
from typing import Annotated, Any, Literal

from pydantic import Field, field_validator
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
    generation_task_execution_backend: Literal["background_tasks", "celery"] = (
        "background_tasks"
    )
    celery_broker_url: str = "redis://127.0.0.1:6379/0"
    celery_result_backend: str = "redis://127.0.0.1:6379/1"
    text_queue_concurrency: int = Field(default=6, ge=1, le=64)
    image_queue_concurrency: int = Field(default=4, ge=1, le=32)
    video_queue_concurrency: int = Field(default=4, ge=1, le=16)
    callback_queue_concurrency: int = Field(default=3, ge=1, le=16)
    generation_task_target_concurrent_users: int = Field(default=30, ge=1, le=1000)
    generation_task_recovery_enabled: bool = True
    generation_task_recovery_interval_seconds: float = Field(default=60.0, ge=5, le=3600)
    generation_task_queued_stale_seconds: float = Field(default=60.0, ge=1, le=3600)
    generation_task_running_stale_seconds: float = Field(default=1800.0, ge=60, le=86400)
    model_gateway_image_model: str | None = None
    model_gateway_image_size: str = "1024x1024"
    model_gateway_image_response_format: str | None = None
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
    volcengine_video_resolution: str = "720p"
    volcengine_video_image_mode: Literal["first_last_frame", "reference_images"] = (
        "first_last_frame"
    )
    volcengine_video_min_duration_seconds: int = Field(default=4, ge=1, le=300)
    volcengine_video_max_duration_seconds: int = Field(default=12, ge=1, le=300)
    volcengine_video_max_reference_images: int = Field(default=2, ge=1, le=20)
    volcengine_video_generate_audio: bool = True
    volcengine_video_watermark: bool = False
    volcengine_video_return_last_frame: bool = False
    volcengine_video_execution_expires_after: int = Field(default=172800, ge=3600, le=259200)
    volcengine_video_priority: int = Field(default=0, ge=0, le=9)
    volcengine_video_safety_identifier: str | None = None

    image_provider: Literal["placeholder", "volcengine", "gateway"] = "placeholder"
    video_provider: Literal["placeholder", "volcengine"] = "placeholder"
    object_storage_provider: Literal["local", "s3", "r2", "minio"] = "local"
    public_base_url: str = "http://127.0.0.1:8001"
    ad_generation_review_base_url: str = "http://127.0.0.1:5173"
    local_storage_root: str = "storage"
    image_download_timeout_seconds: float = 60.0
    image_download_max_bytes: int = 25 * 1024 * 1024
    video_download_timeout_seconds: float = 120.0
    video_download_max_bytes: int = 500 * 1024 * 1024

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("model_gateway_text_models", "model_gateway_image_models", mode="before")
    @classmethod
    def parse_model_gateway_models(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [model.strip() for model in value.split(",") if model.strip()]
        return value

@lru_cache
def get_settings() -> Settings:
    return Settings()
