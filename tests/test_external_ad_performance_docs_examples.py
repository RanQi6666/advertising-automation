from __future__ import annotations

import json
from pathlib import Path

from backend.app.schemas.external_ad_performance_analysis import ExternalAdPerformanceAnalysisCreate
from backend.app.schemas.facebook_ad_analysis import FacebookAdAnalysisResult

_SAMPLE_FILENAMES = [
    "外部系统投放数据分析请求示例.json",
    "投放分析_图片广告测试包.json",
    "投放分析_视频广告测试包.json",
    "投放分析_轮播广告测试包.json",
]

_INTEGRATION_DOC_PATH = Path("docs") / "外部系统投放数据分析对接说明.md"
_SUCCEEDED_RESPONSE_START = "<!-- SUCCEEDED_GET_RESPONSE_START -->"
_SUCCEEDED_RESPONSE_END = "<!-- SUCCEEDED_GET_RESPONSE_END -->"
_SIBLING_EXAMPLE_START = "<!-- SIBLING_EXAMPLE_START -->"
_SIBLING_EXAMPLE_END = "<!-- SIBLING_EXAMPLE_END -->"
_EXPECTED_RESULT_KEYS = {
    "schema_version",
    "platform",
    "summary",
    "overall_decision",
    "targeting_analysis",
    "adjustment_plans",
    "copywriting_analysis",
    "media_analysis",
    "market_intelligence",
    "data_gaps",
}
_STANDARD_ADSET_FIELDS = {
    "id",
    "name",
    "status",
    "daily_budget",
    "bid_strategy",
    "billing_event",
    "optimization_goal",
    "pixel_id",
    "custom_event_type",
    "promoted_object",
    "countries",
    "age_min",
    "age_max",
    "genders",
    "audience",
    "device_platforms",
    "placements",
    "scale_threshold",
    "stop_threshold",
    "switch_time",
}
_STANDARD_CREATIVE_FIELDS = {
    "id",
    "name",
    "creative_type",
    "image_url",
    "video_url",
    "image_urls",
    "message",
    "headline",
    "description",
    "btn_type",
    "link",
    "ad_status",
    "page_id",
    "asset_id",
    "asset_name",
    "asset_type",
    "storyboard",
}
_RETIRED_META_AND_INSIGHT_LINK_FIELDS = {
    "fb_id",
    "facebook_ad_id",
    "campaign_id",
    "campaign_name",
    "adset_id",
    "adset_name",
    "ad_id",
    "ad_name",
}


def _extract_marked_json(markdown: str, start_marker: str, end_marker: str) -> dict:
    assert start_marker in markdown, f"missing documentation marker: {start_marker}"
    assert end_marker in markdown, f"missing documentation marker: {end_marker}"

    marked_section = markdown.split(start_marker, maxsplit=1)[1].split(
        end_marker, maxsplit=1
    )[0]
    assert "```json" in marked_section, "marked response must contain a JSON code fence"
    json_text = marked_section.split("```json", maxsplit=1)[1].split("```", maxsplit=1)[0]
    return json.loads(json_text)


def test_external_ad_performance_sample_payloads_match_async_contract():
    for filename in _SAMPLE_FILENAMES:
        path = Path("docs") / filename
        payload = json.loads(path.read_text(encoding="utf-8"))

        assert payload.get("external_request_id"), f"{filename} must include external_request_id"
        assert set(payload.get("adset") or {}).issubset(_STANDARD_ADSET_FIELDS), (
            f"{filename} sample payload should use only documented standard adset fields"
        )
        creative = payload.get("creative") or {}
        assert set(creative).issubset(_STANDARD_CREATIVE_FIELDS), (
            f"{filename} sample payload should use only documented standard creative fields"
        )
        if "genders" in payload.get("adset", {}):
            assert payload["adset"]["genders"] in {"ALL", "MALE", "FEMALE"}
        assert "thumbnail_url" not in creative, (
            f"{filename} sample payload should use documented standard creative fields"
        )
        assert "video_keyframes" not in creative, (
            f"{filename} sample payload should use documented standard creative fields"
        )

        model = ExternalAdPerformanceAnalysisCreate.model_validate(payload)
        if creative.get("creative_type") == "image":
            assert model.creative.get("image_url", "").startswith(("http://", "https://"))
        if creative.get("creative_type") == "video":
            assert model.creative.get("video_url", "").startswith(("http://", "https://"))
        if creative.get("creative_type") == "carousel":
            assert len(model.creative.get("image_urls", [])) >= 2
            assert all(
                url.startswith(("http://", "https://"))
                for url in model.creative["image_urls"]
            )
        assert "duration_seconds" not in creative, (
            f"{filename} sample payload should use documented standard creative fields"
        )
        insight = payload.get("insight") or {}
        assert not (set(insight) & _RETIRED_META_AND_INSIGHT_LINK_FIELDS), (
            f"{filename} sample payload should not duplicate ad-object identities in insight"
        )
        if "status" in insight:
            assert insight["status"] in {"ACTIVE", "PAUSED"}, (
                f"{filename} insight.status must use a supported value when provided"
            )
        assert "action_values" not in insight, (
            f"{filename} sample payload should use documented standard insight fields"
        )
        for sibling in payload.get("siblings") or []:
            sibling_insight = sibling.get("insight") if isinstance(sibling, dict) else None
            if isinstance(sibling_insight, dict):
                assert "action_values" not in sibling_insight, (
                    f"{filename} sibling sample should use documented standard insight fields"
                )


def test_documented_succeeded_get_response_matches_operator_result_contract():
    markdown = _INTEGRATION_DOC_PATH.read_text(encoding="utf-8")
    response = _extract_marked_json(
        markdown,
        _SUCCEEDED_RESPONSE_START,
        _SUCCEEDED_RESPONSE_END,
    )

    assert response["code"] == 0
    assert response["data"]["status"] == "succeeded"
    assert response["data"]["stage"] == "completed"
    assert response["data"]["progress"] == 100
    assert response["data"]["error"] is None

    result = response["data"]["result"]
    assert set(result) == _EXPECTED_RESULT_KEYS
    assert result["schema_version"] == "facebook_ad_analysis_v1"
    assert result["platform"] == "facebook"
    assert len(result["summary"]) <= 100

    decision = result["overall_decision"]
    assert set(decision) == {"action", "priority", "main_problem"}
    assert decision["action"] in {"scale", "optimize", "monitor", "pause"}
    assert decision["priority"] in {"high", "medium", "low"}

    assert len(result["targeting_analysis"]) <= 3
    for item in result["targeting_analysis"]:
        assert set(item) == {
            "dimension",
            "current",
            "decision",
            "problem",
            "suggestion",
            "reason",
        }
        assert item["dimension"] in {
            "country",
            "audience",
            "age",
            "gender",
            "device",
            "placement",
        }
        assert item["decision"] in {"adjust", "test", "monitor"}

    assert 1 <= len(result["adjustment_plans"]) <= 5
    for plan in result["adjustment_plans"]:
        assert set(plan) == {
            "priority",
            "category",
            "title",
            "action",
            "reason",
            "expected_effect",
            "what_to_watch",
        }
        assert plan["priority"] in {"high", "medium", "low"}
        assert plan["category"] in {
            "landing_page",
            "tracking",
            "copywriting",
            "media",
            "targeting",
            "budget",
            "campaign_setup",
        }

    copywriting = result["copywriting_analysis"]
    assert len(copywriting["problems"]) <= 3
    assert len(copywriting["suggestions"]) <= 3

    media = result["media_analysis"]
    assert media["media_type"] in {"image", "video"}
    assert len(media["improvements"]) <= 3

    market = result["market_intelligence"]
    assert market["status"] in {"completed", "partial", "unavailable"}
    assert len(market["references"]) <= 3
    assert len(result["data_gaps"]) <= 3
    FacebookAdAnalysisResult.model_validate(result)


def test_documented_sibling_example_uses_minimal_comparison_contract():
    markdown = _INTEGRATION_DOC_PATH.read_text(encoding="utf-8")
    example = _extract_marked_json(
        markdown,
        _SIBLING_EXAMPLE_START,
        _SIBLING_EXAMPLE_END,
    )

    siblings = example["siblings"]
    assert len(siblings) == 2
    for sibling in siblings:
        assert set(sibling) == {"creative", "insight"}
        assert sibling["creative"].get("name")
        assert sibling["creative"].get("creative_type") in {"image", "video", "carousel"}
        insight = sibling["insight"]
        if "status" in insight:
            assert insight["status"] in {"ACTIVE", "PAUSED"}
        assert "cost_per_action_type" not in insight
        assert "action_values" not in insight
        assert "image_url" not in sibling["creative"]
        assert "video_url" not in sibling["creative"]


def test_integration_documentation_does_not_publish_retired_meta_or_insight_link_ids():
    markdown = _INTEGRATION_DOC_PATH.read_text(encoding="utf-8")

    for field_name in _RETIRED_META_AND_INSIGHT_LINK_FIELDS:
        assert f"`{field_name}`" not in markdown
