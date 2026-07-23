from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AdResearchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    external_user_id: str = Field(min_length=1, max_length=128)
    country: str = Field(min_length=2, max_length=8)
    category: str = Field(min_length=1, max_length=128)
    keywords: list[str] = Field(default_factory=list, max_length=24)
    target_count: int = Field(default=25, ge=1, le=25)

    @field_validator("external_user_id", "country", "category")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("field must not be blank")
        return value

    @field_validator("country")
    @classmethod
    def normalize_country(cls, value: str) -> str:
        return value.upper()

    @field_validator("keywords")
    @classmethod
    def normalize_keywords(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = str(value).strip()
            if not normalized:
                continue
            key = normalized.casefold()
            if key not in seen:
                result.append(normalized)
                seen.add(key)
        return result


class AdResearchCreateResponse(BaseModel):
    task_id: str
    external_user_id: str
    status: str
    idempotent_replay: bool = False
    poll_url: str
    poll_after_seconds: int = 3


class AdResearchPollResponse(BaseModel):
    task_id: str
    external_user_id: str | None = None
    status: str = Field(description="completed, failed, expired, or legacy insufficient status.")
    stage: str
    round: int
    progress: dict[str, Any] = Field(default_factory=dict)
    poll_after_seconds: int | None = None
    research_summary: dict[str, Any] = Field(default_factory=dict)
    ads: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "Completed responses contain exactly target_count ads; failed responses use null; "
            "insufficient is legacy-only and may retain historical ads."
        ),
    )
    result_expires_at: datetime | None = None
    error: dict[str, str] | None = Field(
        default=None, description="Redacted failure details for failed tasks."
    )


class CollectorAd(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ad_library_id: str
    advertiser_name: str | None = None
    status: str | None = None
    days_running: int | None = None
    text_variants: list[str] = Field(default_factory=list)
    headline: str | None = None
    cta_text: str | None = None
    landing_url: str | None = None
    video_url: str | None = None
    thumbnail_url: str | None = None
    duration_seconds: float | None = None
    platforms: list[str] = Field(default_factory=list)
    ad_snapshot_url: str | None = None
    reported_spend_range: dict[str, Any] | None = None
