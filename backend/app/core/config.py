from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ad_automation"
    sync_database_url: str | None = None
    create_db_on_startup: bool = False

    secret_key: str = "change-me-in-production"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000", "http://localhost:5173"]
    )

    llm_provider: Literal["mock", "openai"] = "mock"
    llm_model: str = "gpt-4.1-mini"
    openai_api_key: str | None = None

    image_provider: Literal["placeholder"] = "placeholder"
    object_storage_provider: Literal["local", "s3", "r2", "minio"] = "local"
    local_storage_root: str = "storage"

    facebook_dry_run: bool = True
    facebook_graph_api_base_url: str = "https://graph.facebook.com"
    facebook_graph_api_version: str = "v24.0"

    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, value: str | list[str]) -> list[str]:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
