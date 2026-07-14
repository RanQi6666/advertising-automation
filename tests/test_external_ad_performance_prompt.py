import base64

import pytest

from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.integrations.llm.openai_provider import (
    _ad_performance_analysis_from_data,
    _ad_performance_analysis_system_prompt,
    _ad_performance_user_content,
)
from backend.app.services.external_ad_performance_analysis_service import (
    ExternalAdPerformanceAnalysisService,
)


def test_ad_performance_prompt_sets_public_research_boundaries():
    prompt = _ad_performance_analysis_system_prompt()

    assert "caller-submitted verified Facebook delivery metrics" in prompt
    assert "system-collected public similar-ad creative/proxy signals" in prompt
    assert "executable optimization advice" in prompt
    assert "Never claim public sources verify CTR, CPC, CPA, purchases, revenue, or ROAS" in prompt


def test_ad_performance_prompt_requires_concise_operator_contract_and_safeguards():
    prompt = _ad_performance_analysis_system_prompt()

    for section in (
        "summary",
        "overall_decision",
        "targeting_analysis",
        "adjustment_plans",
        "copywriting_analysis",
        "media_analysis",
        "market_intelligence",
        "data_gaps",
    ):
        assert section in prompt

    assert (
        "targeting_analysis must contain only meaningful issues and at most three items"
        in prompt
    )
    assert "adjustment_plans must contain one to five concrete operator actions" in prompt
    assert (
        "Do not claim country, age, gender, device, or placement performance without "
        "caller-submitted breakdown data"
    ) in prompt
    assert "Public research cannot verify actual performance" in prompt
    assert "If media processing failed, do not invent visual observations" in prompt
    assert (
        "Use Simplified Chinese for operator-facing analysis, except recommended ad copy "
        "must preserve the source ad language"
    ) in prompt


def test_ad_performance_provider_normalizes_new_sections_and_applies_hard_caps():
    data = {
        "summary": "总" * 130,
        "overall_decision": {
            "action": "scale",
            "priority": "critical",
            "main_problem": "问题" * 80,
        },
        "targeting_analysis": [
            {
                "dimension": "age",
                "current": "18-65",
                "decision": "adjust",
                "problem": f"问题{index}",
                "suggestion": f"建议{index}",
                "reason": f"原因{index}",
            }
            for index in range(5)
        ],
        "adjustment_plans": [
            {
                "priority": "high",
                "category": "media",
                "title": f"动作{index}",
                "action": f"执行{index}",
                "reason": f"依据{index}",
                "expected_effect": f"效果{index}",
                "what_to_watch": f"观察{index}",
            }
            for index in range(7)
        ],
        "copywriting_analysis": {
            "summary": "文案摘要",
            "problems": [f"问题{index}" for index in range(5)],
            "suggestions": [f"建议{index}" for index in range(5)],
            "recommended_primary_text": "Keep the original language.",
            "recommended_headline": None,
            "recommended_description": "Description",
        },
        "media_analysis": {
            "media_type": "video",
            "summary": "素材摘要",
            "improvements": [
                {
                    "location": f"{index}s",
                    "problem": f"画面问题{index}",
                    "action": f"素材动作{index}",
                }
                for index in range(5)
            ],
        },
        "market_intelligence": {
            "status": "completed",
            "summary": "市场摘要",
            "references": [
                {
                    "advertiser_name": f"广告主{index}",
                    "source_url": f"https://example.com/{index}",
                    "observed_pattern": f"模式{index}",
                    "applicable_idea": f"借鉴{index}",
                }
                for index in range(5)
            ],
            "limitation": "公开来源不能证明真实投放成效。",
        },
        "data_gaps": [f"缺口{index}" for index in range(5)],
        "recommended_actions": ["旧字段不得继续输出"],
        "visual_analysis": {"summary": "旧字段不得继续输出"},
    }

    normalized = _ad_performance_analysis_from_data(data)

    assert set(normalized) == {
        "summary",
        "overall_decision",
        "targeting_analysis",
        "adjustment_plans",
        "copywriting_analysis",
        "media_analysis",
        "market_intelligence",
        "data_gaps",
    }
    assert len(normalized["summary"]) == 100
    assert normalized["overall_decision"]["priority"] == "high"
    assert len(normalized["overall_decision"]["main_problem"]) == 100
    assert len(normalized["targeting_analysis"]) == 3
    assert len(normalized["adjustment_plans"]) == 5
    assert len(normalized["copywriting_analysis"]["problems"]) == 3
    assert len(normalized["copywriting_analysis"]["suggestions"]) == 3
    assert len(normalized["media_analysis"]["improvements"]) == 3
    assert len(normalized["market_intelligence"]["references"]) == 3
    assert len(normalized["data_gaps"]) == 3


@pytest.mark.asyncio
async def test_external_analysis_service_advertises_operator_sections_and_rule_owned_facts(
    monkeypatch,
):
    captured = {}

    class Provider:
        async def analyze_ad_performance(self, context):
            captured.update(context)
            return {"summary": "完成"}

    monkeypatch.setattr(
        "backend.app.services.external_ad_performance_analysis_service.get_llm_provider",
        lambda: Provider(),
    )

    await ExternalAdPerformanceAnalysisService()._safe_llm_contribution(
        payload={"creative": {"creative_type": "image"}},
        rule_analysis={"metrics": {"spend": {"value": 1}}},
        media_summary={"status": "available"},
        research_summary={"status": "unavailable"},
    )

    assert captured["result_contract"] == {
        "schema_version": "facebook_ad_analysis_v1",
        "operator_sections": [
            "summary",
            "overall_decision",
            "targeting_analysis",
            "adjustment_plans",
            "copywriting_analysis",
            "media_analysis",
            "market_intelligence",
            "data_gaps",
        ],
        "rule_owned_facts": [
            "metrics",
            "primary_bottleneck",
            "overall_action",
            "overall_priority",
            "data_quality",
        ],
    }


def test_video_keyframes_are_attached_as_local_visual_inputs_when_video_is_unsupported(tmp_path):
    frame_paths = []
    for index in range(4):
        frame = tmp_path / f"frame-{index}.jpg"
        frame.write_bytes(b"\xff\xd8\xff" + bytes([index]))
        frame_paths.append(str(frame))

    content = _ad_performance_user_content(
        {
            "creative": {
                "creative_type": "video",
                "video_url": "https://newpixel.messrocts.com/uploads/ad.mp4",
            },
            "media_summary": {
                "status": "available",
                "local_artifacts": {
                    "keyframe_paths": frame_paths,
                },
            },
        },
        supports_video_input=False,
    )

    assert isinstance(content, list)
    images = [item for item in content if item.get("type") == "image_url"]
    assert len(images) == 3
    assert all(item["image_url"]["url"].startswith("data:image/jpeg;base64,") for item in images)
    assert base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1]) == (
        b"\xff\xd8\xff\x00"
    )
    assert "local_artifacts" not in content[0]["text"]
    assert str(tmp_path) not in content[0]["text"]
    assert "media_analysis" in content[0]["text"]
    assert "visual_analysis" not in content[0]["text"]


def test_image_thumbnail_is_preferred_over_remote_image_url(tmp_path):
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"\xff\xd8\xffthumbnail")

    content = _ad_performance_user_content(
        {
            "creative": {
                "creative_type": "image",
                "image_url": "https://newpixel.messrocts.com/uploads/ad.jpg",
            },
            "media_summary": {
                "status": "available",
                "local_artifacts": {"thumbnail_path": str(thumbnail)},
            },
        }
    )

    assert isinstance(content, list)
    images = [item for item in content if item.get("type") == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "https://newpixel.messrocts.com/uploads/ad.jpg" not in [
        item["image_url"]["url"] for item in images
    ]


def test_missing_local_visual_artifacts_are_skipped_safely():
    content = _ad_performance_user_content(
        {
            "creative": {"creative_type": "video", "video_url": "https://example.com/ad.mp4"},
            "media_summary": {
                "local_artifacts": {"keyframe_paths": ["Z:/does-not-exist/frame.jpg"]}
            },
        },
        supports_video_input=False,
    )

    assert isinstance(content, str)
    assert "local_artifacts" not in content


@pytest.mark.asyncio
async def test_mock_provider_returns_operator_result_without_inventing_media_or_breakdowns():
    result = await MockLLMProvider().analyze_ad_performance(
        {
            "creative": {
                "creative_type": "video",
                "video_url": "https://newpixel.messrocts.com/uploads/ad.mp4",
            },
            "metrics": {"spend": 100, "ctr": 0.5},
            "rule_analysis": {"problems": []},
            "result_contract": {
                "schema_version": "facebook_ad_analysis_v1",
                "operator_sections": ["summary"],
            },
        }
    )

    assert set(result) == {
        "summary",
        "overall_decision",
        "targeting_analysis",
        "adjustment_plans",
        "copywriting_analysis",
        "media_analysis",
        "market_intelligence",
        "data_gaps",
    }
    assert result["targeting_analysis"] == []
    assert result["media_analysis"]["summary"] == "Mock 环境不进行真实画面识别。"
    assert "visual_analysis" not in result
