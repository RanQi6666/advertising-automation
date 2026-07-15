from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.schemas.external_ad_performance_analysis import (
    ExternalAdPerformanceAnalysisCreate,
    ad_analysis_payload_hash,
    canonicalize_ad_analysis_payload,
    is_switch_time_active,
)


def _payload() -> dict:
    return {
        "external_request_id": " ad-analysis-20260713-000001 ",
        "source_type": "external",
        "campaign": {},
        "adset": {},
        "creative": {
            "creative_type": "image",
            "image_url": "https://newpixel.messrocts.com/uploads/a.jpg",
            "thumbnail_url": "https://caller.example/thumb.jpg",
            "video_keyframes": [{"second": 0, "image_url": "https://caller.example/0.jpg"}],
            "future_meta_field": "preserved",
        },
        "insight": {
            "status": "ACTIVE",
            "spend": "0.2400",
            "actions": [
                {"action_type": "link_click", "value": "86.0"},
                {"action_type": "landing_page_view", "value": "23.00"},
            ],
        },
        "siblings": [],
        "unknown_top_level": {"keep": True},
    }


def test_canonical_payload_ignores_server_generated_media_fields_and_stabilizes_order():
    first = ExternalAdPerformanceAnalysisCreate.model_validate(_payload())
    reordered = deepcopy(_payload())
    reordered["insight"]["actions"].reverse()
    reordered["insight"]["spend"] = "0.24"
    second = ExternalAdPerformanceAnalysisCreate.model_validate(reordered)

    normalized = canonicalize_ad_analysis_payload(first)

    assert first.external_request_id == "ad-analysis-20260713-000001"
    assert normalized["creative"]["future_meta_field"] == "preserved"
    assert "thumbnail_url" not in normalized["creative"]
    assert "video_keyframes" not in normalized["creative"]
    assert "external_request_id" not in normalized
    assert ad_analysis_payload_hash(first) == ad_analysis_payload_hash(second)


@pytest.mark.parametrize(
    ("creative", "message"),
    [
        ({"creative_type": "image"}, "creative.image_url is required"),
        ({"creative_type": "video"}, "creative.video_url is required"),
        (
            {"creative_type": "image", "image_url": "file:///tmp/a.jpg"},
            "must be a complete HTTP or HTTPS URL",
        ),
    ],
)
def test_media_contract_rejects_missing_or_non_http_source(creative, message):
    payload = _payload()
    payload["creative"] = creative
    with pytest.raises(ValidationError, match=message):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)


def test_media_contract_accepts_any_public_http_or_https_host_and_normalizes_button_type():
    payload = _payload()
    payload["creative"]["image_url"] = "http://cdn.other-system.example/path/ad.jpg"

    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    assert request.creative["image_url"] == "http://cdn.other-system.example/path/ad.jpg"


def test_carousel_contract_accepts_multiple_http_or_https_images_and_normalizes_button_type():
    payload = _payload()
    payload["creative"] = {
        "creative_type": "carousel",
        "image_urls": [
            "http://cdn.other-system.example/cards/one.jpg",
            "https://storage.example.net/cards/two.webp",
        ],
        "btn_type": "了解更多",
    }

    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    assert request.creative["creative_type"] == "carousel"
    assert request.creative["image_urls"] == payload["creative"]["image_urls"]
    assert request.creative["btn_type"] == "LEARN_MORE"


@pytest.mark.parametrize(
    ("creative", "message"),
    [
        ({"creative_type": "carousel"}, "creative.image_urls is required"),
        (
            {"creative_type": "carousel", "image_urls": "https://example.test/a.jpg"},
            "creative.image_urls must be an array",
        ),
        (
            {"creative_type": "carousel", "image_urls": ["https://example.test/a.jpg"]},
            "between 2 and 10 image URLs",
        ),
        (
            {
                "creative_type": "carousel",
                "image_urls": [
                    "https://example.test/a.jpg",
                    "file:///tmp/b.jpg",
                ],
            },
            r"creative\.image_urls\[1\] must be a complete HTTP or HTTPS URL",
        ),
        (
            {
                "creative_type": "image",
                "image_url": "https://example.test/a.jpg",
                "btn_type": "BUY_THE_MOON",
            },
            "creative.btn_type must be one of",
        ),
    ],
)
def test_media_contract_rejects_invalid_carousel_or_button_type(creative, message):
    payload = _payload()
    payload["creative"] = creative
    with pytest.raises(ValidationError, match=message):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)


@pytest.mark.parametrize(
    ("input_value", "normalized"),
    [
        ("Video Call", "VIDEO_CALL"),
        ("Install Mobile App", "INSTALL_MOBILE_APP"),
        ("Use Mobile App", "USE_MOBILE_APP"),
        ("Mobile Download", "MOBILE_DOWNLOAD"),
        ("Book Travel", "BOOK_TRAVEL"),
        ("Listen Music", "LISTEN_MUSIC"),
        ("观看视频", "WATCH_MORE"),
        ("LEARN_MORE", "LEARN_MORE"),
    ],
)
def test_button_type_accepts_display_labels_and_meta_codes(input_value, normalized):
    payload = _payload()
    payload["creative"]["btn_type"] = input_value

    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    assert request.creative["btn_type"] == normalized


def test_allows_unlisted_legacy_fields_without_making_them_part_of_the_contract():
    payload = _payload()
    payload["callback_url"] = "https://example.com/cb"
    payload["external_account_id"] = "acct-1"
    payload["account_currency"] = "USD"
    payload["external_user_id"] = "legacy-user"
    payload["campaign"]["fb_id"] = "meta-campaign-legacy"
    payload["adset"]["fb_id"] = "meta-adset-legacy"
    payload["creative"]["facebook_ad_id"] = "meta-ad-legacy"
    payload["insight"]["campaign_id"] = "meta-campaign-legacy"
    payload["insight"]["ad_id"] = "meta-ad-legacy"
    payload["insight"]["action_values"] = [
        {"action_type": "purchase", "value": "250.00"}
    ]
    payload["insight"]["cost_per_action_type"] = [
        {"action_type": "purchase", "value": "6.67"}
    ]

    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    assert request.external_request_id == "ad-analysis-20260713-000001"
    assert "external_user_id" not in ExternalAdPerformanceAnalysisCreate.model_fields
    assert request.campaign["fb_id"] == "meta-campaign-legacy"
    assert request.insight["ad_id"] == "meta-ad-legacy"


def test_insight_status_is_normalized_while_unlisted_values_are_accepted():
    payload = _payload()
    payload["insight"]["status"] = " paused "

    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    assert request.insight["status"] == "PAUSED"

    payload["insight"]["action_values"] = [
        {"action_type": "purchase", "value": "250.00"}
    ]
    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)
    assert request.insight["action_values"][0]["value"] == "250.00"


def test_insight_accepts_unlisted_precalculated_cost_per_action_type():
    payload = _payload()
    payload["insight"]["cost_per_action_type"] = [
        {"action_type": "purchase", "value": "6.67"}
    ]

    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    assert request.insight["cost_per_action_type"][0]["value"] == "6.67"


@pytest.mark.parametrize(
    "status",
    ["DELETED", "ARCHIVED", "PENDING_REVIEW", "DISAPPROVED", "LIMITED_BY_A_WIZARD"],
)
def test_insight_status_rejects_non_operator_delivery_state(status):
    payload = _payload()
    payload["insight"]["status"] = status

    with pytest.raises(ValidationError, match="insight.status must be one of"):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)


def test_insight_status_is_optional_for_the_performance_snapshot():
    payload = _payload()
    del payload["insight"]["status"]

    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    assert "status" not in request.insight


def test_cbo_campaign_budget_and_adset_budget_state_are_accepted():
    payload = _payload()
    payload["campaign"] = {
        "daily_budget": "5000",
        "bid_strategy": "LOWEST_COST_WITH_MIN_ROAS",
        "switch_time": "9-18",
    }
    payload["adset"] = {
        "daily_budget": None,
        "optimization_goal": "OFFSITE_CONVERSIONS",
    }

    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    assert request.campaign["daily_budget"] == "5000"
    assert request.campaign["bid_strategy"] == "LOWEST_COST_WITH_MIN_ROAS"
    assert request.campaign["switch_time"] == "9-18"
    assert request.adset["daily_budget"] is None


def test_abo_adset_budget_permits_its_bid_strategy_and_delivery_controls():
    payload = _payload()
    payload["adset"] = {
        "daily_budget": "800",
        "bid_strategy": "LOWEST_COST_WITH_BID_CAP",
        "billing_event": "LINK_CLICKS",
        "optimization_goal": "LANDING_PAGE_VIEWS",
        "custom_event_type": "COMPLETE-REGISTRATION",
        "scale_threshold": "8.50",
        "stop_threshold": "20.00",
        "switch_time": "9-18",
    }

    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    assert request.adset["bid_strategy"] == "LOWEST_COST_WITH_BID_CAP"
    assert request.adset["custom_event_type"] == "COMPLETE_REGISTRATION"


@pytest.mark.parametrize(
    ("input_value", "normalized"),
    [
        ("all", "ALL"),
        ("male", "MALE"),
        ("FEMALE", "FEMALE"),
    ],
)
def test_adset_genders_uses_the_documented_standard_values(input_value, normalized):
    payload = _payload()
    payload["adset"]["genders"] = input_value

    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    assert request.adset["genders"] == normalized


def test_adset_genders_rejects_values_outside_the_documented_standard():
    payload = _payload()
    payload["adset"]["genders"] = "1"

    with pytest.raises(ValidationError, match="adset.genders must be one of"):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)


def test_cbo_contract_rejects_adset_bid_strategy_and_unknown_campaign_strategy():
    payload = _payload()
    payload["adset"]["daily_budget"] = None
    payload["adset"]["bid_strategy"] = "LOWEST_COST_WITHOUT_CAP"
    with pytest.raises(
        ValidationError,
        match="adset.bid_strategy requires a non-null adset.daily_budget",
    ):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    payload = _payload()
    payload["campaign"]["bid_strategy"] = "MANUAL_BID"
    with pytest.raises(ValidationError, match="campaign.bid_strategy must be one of"):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)

    payload = _payload()
    payload["campaign"]["switch_time"] = "18-9"
    request = ExternalAdPerformanceAnalysisCreate.model_validate(payload)
    assert request.campaign["switch_time"] == "18-9"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("billing_event", "POST_ENGAGEMENT", "adset.billing_event must be one of"),
        ("optimization_goal", "CONVERSIONS", "adset.optimization_goal must be one of"),
        ("custom_event_type", "SUBSCRIBE", "adset.custom_event_type must be one of"),
        ("switch_time", "24-9", "adset.switch_time must use HH-HH"),
    ],
)
def test_adset_delivery_configuration_rejects_unsupported_values(field, value, message):
    payload = _payload()
    payload["adset"][field] = value
    with pytest.raises(ValidationError, match=message):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)


def test_adset_delivery_configuration_requires_scale_threshold_below_stop_threshold():
    payload = _payload()
    payload["adset"]["scale_threshold"] = "20"
    payload["adset"]["stop_threshold"] = "10"
    with pytest.raises(ValidationError, match="adset.scale_threshold must be lower"):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)


def test_switch_time_algorithm_supports_cross_midnight_windows():
    assert is_switch_time_active("18-9", hour=18) is True
    assert is_switch_time_active("18-9", hour=23) is True
    assert is_switch_time_active("18-9", hour=0) is True
    assert is_switch_time_active("18-9", hour=8) is True
    assert is_switch_time_active("18-9", hour=9) is False
    assert is_switch_time_active("18-9", hour=17) is False
    assert is_switch_time_active("0-0", hour=12) is True

    payload = _payload()
    payload["campaign"]["switch_time"] = "9-9"
    with pytest.raises(ValidationError, match="campaign.switch_time must not use the same"):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)
