from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.schemas.external_ad_performance_analysis import (
    ExternalAdPerformanceAnalysisCreate,
    ad_analysis_payload_hash,
    canonicalize_ad_analysis_payload,
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
            {"creative_type": "image", "image_url": "http://example.test/a.jpg"},
            "must be a complete HTTPS URL",
        ),
    ],
)
def test_media_contract_rejects_missing_or_non_https_source(creative, message):
    payload = _payload()
    payload["creative"] = creative
    with pytest.raises(ValidationError, match=message):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)


def test_rejects_callback_and_external_account_contract_expansion():
    payload = _payload()
    payload["callback_url"] = "https://example.com/cb"
    payload["external_account_id"] = "acct-1"
    with pytest.raises(ValidationError, match="unsupported fields"):
        ExternalAdPerformanceAnalysisCreate.model_validate(payload)
