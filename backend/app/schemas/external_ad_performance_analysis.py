from __future__ import annotations

import hashlib
import json
import re
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
    "daily_budget",
    "lifetime_budget",
}
_IGNORED_CREATIVE_KEYS = {"thumbnail_url", "video_keyframes"}
_CBO_BID_STRATEGIES = {
    "LOWEST_COST_WITHOUT_CAP",
    "LOWEST_COST_WITH_BID_CAP",
    "LOWEST_COST_WITH_MIN_ROAS",
}
_ADSET_BILLING_EVENTS = {"IMPRESSIONS", "LINK_CLICKS"}
_ADSET_OPTIMIZATION_GOALS = {
    "LINK_CLICKS",
    "LANDING_PAGE_VIEWS",
    "OFFSITE_CONVERSIONS",
    "THRUPLAY",
    "APP_INSTALLS",
    "LEAD_GENERATION",
}
_ADSET_GENDERS = {"ALL", "MALE", "FEMALE"}
_ADSET_CUSTOM_EVENT_TYPES = {
    "COMPLETE_REGISTRATION",
    "PURCHASE",
    "ADD_TO_CART",
    "INITIATED_CHECKOUT",
    "SEARCH",
    "ADD_PAYMENT_INFO",
    "FIRST_RECHARGE",
}
_INSIGHT_DELIVERY_STATUSES = {
    "ACTIVE",
    "PAUSED",
}
_CREATIVE_BTN_TYPE_ALIASES = {
    "VIDEO CALL": "VIDEO_CALL",
    "VIDEO_CALL": "VIDEO_CALL",
    "INSTALL MOBILE APP": "INSTALL_MOBILE_APP",
    "INSTALL_MOBILE_APP": "INSTALL_MOBILE_APP",
    "USE MOBILE APP": "USE_MOBILE_APP",
    "USE_MOBILE_APP": "USE_MOBILE_APP",
    "MOBILE DOWNLOAD": "MOBILE_DOWNLOAD",
    "MOBILE_DOWNLOAD": "MOBILE_DOWNLOAD",
    "BOOK TRAVEL": "BOOK_TRAVEL",
    "BOOK_TRAVEL": "BOOK_TRAVEL",
    "LISTEN MUSIC": "LISTEN_MUSIC",
    "LISTEN_MUSIC": "LISTEN_MUSIC",
    "观看视频": "WATCH_MORE",
    "WATCH MORE": "WATCH_MORE",
    "WATCH_MORE": "WATCH_MORE",
    "了解更多": "LEARN_MORE",
    "LEARN MORE": "LEARN_MORE",
    "LEARN_MORE": "LEARN_MORE",
}
_SWITCH_TIME_PATTERN = re.compile(r"^(\d{1,2})-(\d{1,2})$")
class ExternalAdPerformanceAnalysisCreate(BaseModel):
    """External one-JSON Facebook ad analysis request.

    The caller owns only ``external_request_id`` and raw business/media data. Server-generated
    fields such as thumbnails and keyframes are intentionally ignored for idempotency.
    """

    model_config = ConfigDict(extra="allow")

    external_request_id: str = Field(min_length=1, max_length=128)
    source_type: str = "external"
    date_preset: str | None = None
    date_start: str | None = None
    date_stop: str | None = None
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
        self._validate_creative_media()
        self._normalize_creative_button_type()
        self._validate_insight_contract(self.insight, field_name="insight")
        for index, sibling in enumerate(self.siblings):
            if isinstance(sibling, dict) and isinstance(sibling.get("insight"), dict):
                self._validate_insight_contract(
                    sibling["insight"], field_name=f"siblings[{index}].insight"
                )

        bid_strategy = self.campaign.get("bid_strategy")
        if bid_strategy is not None:
            self.campaign["bid_strategy"] = _normalize_enum(
                bid_strategy,
                field_name="campaign.bid_strategy",
                allowed=_CBO_BID_STRATEGIES,
            )
        _normalize_switch_time(self.campaign, field_name="campaign.switch_time")

        self._validate_adset_delivery_configuration()
        return self

    @staticmethod
    def _validate_insight_contract(insight: dict[str, Any], *, field_name: str) -> None:
        status = insight.get("status")
        if status is not None:
            insight["status"] = _normalize_enum(
                status,
                field_name=f"{field_name}.status",
                allowed=_INSIGHT_DELIVERY_STATUSES,
            )

    def _validate_creative_media(self) -> None:
        creative_type = str(self.creative.get("creative_type") or "").strip().lower()
        if creative_type not in {"image", "video", "carousel"}:
            raise ValueError("creative.creative_type must be image, video, or carousel")
        self.creative["creative_type"] = creative_type
        if creative_type == "carousel":
            image_urls = self.creative.get("image_urls")
            if image_urls is None:
                raise ValueError("creative.image_urls is required")
            if not isinstance(image_urls, list):
                raise ValueError("creative.image_urls must be an array")
            if not 2 <= len(image_urls) <= 10:
                raise ValueError("creative.image_urls must contain between 2 and 10 image URLs")
            cleaned_urls: list[str] = []
            for index, image_url in enumerate(image_urls):
                if not isinstance(image_url, str) or not image_url.strip():
                    raise ValueError(
                        f"creative.image_urls[{index}] must be a complete HTTP or HTTPS URL"
                    )
                cleaned_url = image_url.strip()
                _validate_complete_http_url(
                    cleaned_url, field_name=f"creative.image_urls[{index}]"
                )
                cleaned_urls.append(cleaned_url)
            self.creative["image_urls"] = cleaned_urls
            return

        source_field = "image_url" if creative_type == "image" else "video_url"
        source_url = self.creative.get(source_field)
        if not isinstance(source_url, str) or not source_url.strip():
            raise ValueError(f"creative.{source_field} is required")
        cleaned_url = source_url.strip()
        _validate_complete_http_url(cleaned_url, field_name=f"creative.{source_field}")
        self.creative[source_field] = cleaned_url

    def _normalize_creative_button_type(self) -> None:
        btn_type = self.creative.get("btn_type")
        if btn_type is None:
            return
        key = " ".join(str(btn_type).strip().upper().replace("_", " ").split())
        normalized = _CREATIVE_BTN_TYPE_ALIASES.get(key)
        if normalized is None:
            supported = ", ".join(sorted(set(_CREATIVE_BTN_TYPE_ALIASES.values())))
            raise ValueError(f"creative.btn_type must be one of: {supported}")
        self.creative["btn_type"] = normalized

    def _validate_adset_delivery_configuration(self) -> None:
        adset_daily_budget = self.adset.get("daily_budget")
        if "bid_strategy" in self.adset:
            if not _is_positive_decimal(adset_daily_budget):
                raise ValueError(
                    "adset.bid_strategy requires a non-null adset.daily_budget"
                )
            self.adset["bid_strategy"] = _normalize_enum(
                self.adset["bid_strategy"],
                field_name="adset.bid_strategy",
                allowed=_CBO_BID_STRATEGIES,
            )

        for field_name, allowed in (
            ("billing_event", _ADSET_BILLING_EVENTS),
            ("optimization_goal", _ADSET_OPTIMIZATION_GOALS),
            ("genders", _ADSET_GENDERS),
        ):
            if field_name in self.adset and self.adset[field_name] is not None:
                self.adset[field_name] = _normalize_enum(
                    self.adset[field_name],
                    field_name=f"adset.{field_name}",
                    allowed=allowed,
                )

        custom_event_type = self.adset.get("custom_event_type")
        if custom_event_type is not None:
            normalized_event_type = str(custom_event_type).strip().upper().replace("-", "_")
            if normalized_event_type not in _ADSET_CUSTOM_EVENT_TYPES:
                allowed = ", ".join(sorted(_ADSET_CUSTOM_EVENT_TYPES))
                raise ValueError(
                    "adset.custom_event_type must be one of: " + allowed
                )
            self.adset["custom_event_type"] = normalized_event_type

        thresholds: dict[str, Decimal] = {}
        for field_name in ("scale_threshold", "stop_threshold"):
            if field_name not in self.adset or self.adset[field_name] is None:
                continue
            threshold = _non_negative_decimal(
                self.adset[field_name], field_name=f"adset.{field_name}"
            )
            thresholds[field_name] = threshold
            self.adset[field_name] = format(threshold.normalize(), "f")

        if (
            "scale_threshold" in thresholds
            and "stop_threshold" in thresholds
            and thresholds["scale_threshold"] >= thresholds["stop_threshold"]
        ):
            raise ValueError(
                "adset.scale_threshold must be lower than adset.stop_threshold"
            )

        _normalize_switch_time(self.adset, field_name="adset.switch_time")


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


def _validate_complete_http_url(value: str, *, field_name: str) -> None:
    parsed = urlparse(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{field_name} must be a complete HTTP or HTTPS URL")


def _normalize_enum(value: Any, *, field_name: str, allowed: set[str]) -> str:
    normalized = str(value).strip().upper()
    if normalized not in allowed:
        allowed_values = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of: {allowed_values}")
    return normalized


def _non_negative_decimal(value: Any, *, field_name: str) -> Decimal:
    try:
        decimal_value = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field_name} must be a non-negative number") from None
    if not decimal_value.is_finite() or decimal_value < 0:
        raise ValueError(f"{field_name} must be a non-negative number")
    return decimal_value


def _is_positive_decimal(value: Any) -> bool:
    if value is None:
        return False
    try:
        return _non_negative_decimal(value, field_name="adset.daily_budget") > 0
    except ValueError:
        return False


def _normalize_switch_time(record: dict[str, Any], *, field_name: str) -> None:
    switch_time = record.get("switch_time")
    if switch_time is None:
        return
    match = _SWITCH_TIME_PATTERN.fullmatch(str(switch_time).strip())
    if not match:
        raise ValueError(f"{field_name} must use HH-HH with hours from 0 to 23")
    start_hour, end_hour = (int(hour) for hour in match.groups())
    if (
        start_hour > 23
        or end_hour > 23
    ):
        raise ValueError(f"{field_name} must use HH-HH with hours from 0 to 23")
    if start_hour == end_hour and (start_hour != 0 or end_hour != 0):
        raise ValueError(
            f"{field_name} must not use the same start and end hour; use 0-0 for no limit"
        )
    record["switch_time"] = f"{start_hour}-{end_hour}"


def is_switch_time_active(switch_time: str, *, hour: int) -> bool:
    """Return whether a local account hour falls in a recurring daily switch window.

    The interval is start-inclusive/end-exclusive. A start greater than the end
    crosses midnight; ``0-0`` is the explicit all-day sentinel.
    """

    if not 0 <= hour <= 23:
        raise ValueError("hour must be between 0 and 23")
    match = _SWITCH_TIME_PATTERN.fullmatch(str(switch_time).strip())
    if not match:
        raise ValueError("switch_time must use HH-HH with hours from 0 to 23")
    start_hour, end_hour = (int(value) for value in match.groups())
    if start_hour > 23 or end_hour > 23:
        raise ValueError("switch_time must use HH-HH with hours from 0 to 23")
    if start_hour == end_hour:
        if start_hour == 0:
            return True
        raise ValueError("switch_time must not use the same start and end hour")
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour


def _normalize_value(value: Any, key: str | None = None) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        result = {
            str(child_key): _normalize_value(child_value, str(child_key))
            for child_key, child_value in value.items()
            if child_value is not None
        }
        for array_key in ("actions",):
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
