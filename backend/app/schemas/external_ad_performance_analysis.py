from __future__ import annotations

import hashlib
import json
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AD_ANALYSIS_CODE_SUCCESS = 0
AD_ANALYSIS_CODE_ACCEPTED = 1001
AD_ANALYSIS_CODE_VALIDATION_ERROR = 4001
AD_ANALYSIS_CODE_AUTH_ERROR = 4003
AD_ANALYSIS_CODE_SERVER_ERROR = 5001

_NUMERIC_KEYS = {
    "spend",
    "impressions",
    "reach",
    "frequency",
    "clicks",
    "inline_link_clicks",
    "ctr",
    "inline_link_click_ctr",
    "cpc",
    "cpm",
    "value",
    "purchase_value",
    "website_purchase_roas",
    "daily_budget",
    "lifetime_budget",
}
_IGNORED_CREATIVE_KEYS = {"thumbnail_url", "video_keyframes"}
_REJECTED_TOP_LEVEL_KEYS = {
    "callback_url",
    "external_account_id",
    "research_context",
    "search_keywords",
    "competitor_names",
}


class ExternalAdPerformanceAnalysisCreate(BaseModel):
    """External one-JSON Facebook ad analysis request.

    The caller owns only ``external_request_id`` and raw business/media data. Server-generated
    fields such as thumbnails and keyframes are intentionally ignored for idempotency.
    """

    model_config = ConfigDict(extra="allow")

    external_request_id: str = Field(min_length=1, max_length=128)
    source_type: str = "external"
    external_user_id: str | None = None
    date_preset: str | None = None
    date_start: str | None = None
    date_stop: str | None = None
    account_currency: str | None = None
    campaign: dict[str, Any] = Field(default_factory=dict)
    adset: dict[str, Any] = Field(default_factory=dict)
    creative: dict[str, Any] = Field(default_factory=dict)
    insight: dict[str, Any] = Field(default_factory=dict)
    siblings: list[dict[str, Any]] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @field_validator("external_request_id")
    @classmethod
    def strip_external_request_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("external_request_id must not be blank")
        return stripped

    @model_validator(mode="after")
    def validate_external_contract(self) -> ExternalAdPerformanceAnalysisCreate:
        rejected = sorted(set(self.model_extra or {}) & _REJECTED_TOP_LEVEL_KEYS)
        if rejected:
            raise ValueError(f"unsupported fields: {', '.join(rejected)}")

        creative_type = str(self.creative.get("creative_type") or "").strip().lower()
        if creative_type not in {"image", "video"}:
            raise ValueError("creative.creative_type must be image or video")
        source_field = "image_url" if creative_type == "image" else "video_url"
        source_url = self.creative.get(source_field)
        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError(f"creative.{source_field} is required")
        _validate_complete_https_url(source_url, field_name=f"creative.{source_field}")
        return self


class AdAnalysisCreateData(BaseModel):
    analysis_id: str
    external_request_id: str
    status: Literal["queued", "processing", "succeeded", "failed"]
    stage: str
    created_at: str
    poll_url: str
    idempotent_replay: bool = False


class AdAnalysisJobError(BaseModel):
    error_code: str
    message: str
    retryable: bool = False


class AdAnalysisJobData(BaseModel):
    analysis_id: str
    external_request_id: str
    status: Literal["queued", "processing", "succeeded", "failed"]
    stage: str
    progress: int = Field(ge=0, le=100)
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: AdAnalysisJobError | None = None


class AdAnalysisEnvelope(BaseModel):
    code: int
    message: str
    data: dict[str, Any] = Field(default_factory=dict)


def canonicalize_ad_analysis_payload(
    payload: ExternalAdPerformanceAnalysisCreate,
) -> dict[str, Any]:
    raw = payload.model_dump(mode="python", exclude_none=True)
    raw.pop("external_request_id", None)
    creative = raw.get("creative")
    if isinstance(creative, dict):
        raw["creative"] = {
            key: value for key, value in creative.items() if key not in _IGNORED_CREATIVE_KEYS
        }
    normalized = _normalize_value(raw)
    if not isinstance(normalized, dict):
        raise TypeError("normalized ad-analysis payload must be an object")
    return normalized


def ad_analysis_payload_hash(payload: ExternalAdPerformanceAnalysisCreate) -> str:
    canonical_json = json.dumps(
        canonicalize_ad_analysis_payload(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def _validate_complete_https_url(value: str, *, field_name: str) -> None:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError(f"{field_name} must be a complete HTTPS URL")


def _normalize_value(value: Any, key: str | None = None) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        result = {
            str(child_key): _normalize_value(child_value, str(child_key))
            for child_key, child_value in value.items()
            if child_value is not None
        }
        for array_key in ("actions", "cost_per_action_type"):
            items = result.get(array_key)
            if isinstance(items, list):
                result[array_key] = sorted(
                    items,
                    key=lambda item: (
                        str(item.get("action_type") or "") if isinstance(item, dict) else "",
                        str(item.get("value") or "") if isinstance(item, dict) else "",
                    ),
                )
        siblings = result.get("siblings")
        if isinstance(siblings, list):
            result["siblings"] = sorted(siblings, key=_sibling_sort_key)
        return result
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    if key in _NUMERIC_KEYS and isinstance(value, (str, int, float, Decimal)):
        try:
            decimal_value = Decimal(str(value).replace(",", "").strip())
        except (InvalidOperation, ValueError):
            return value
        if not decimal_value.is_finite():
            return value
        return format(decimal_value.normalize(), "f")
    return value


def _sibling_sort_key(value: Any) -> tuple[str, str]:
    if not isinstance(value, dict):
        return "", json.dumps(value, ensure_ascii=False, sort_keys=True)
    creative = value.get("creative") if isinstance(value.get("creative"), dict) else {}
    identity = value.get("facebook_ad_id") or creative.get("fb_id") or creative.get(
        "facebook_ad_id"
    )
    fallback = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(identity or ""), fallback
