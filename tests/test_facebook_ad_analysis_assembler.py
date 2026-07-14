from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.schemas.facebook_ad_analysis import FacebookAdAnalysisResult
from backend.app.services.facebook_ad_analysis_assembler import assemble_facebook_ad_analysis
from backend.app.services.facebook_ad_metrics import build_facebook_metric_analysis

EXPECTED_TOP_LEVEL_KEYS = {
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


def _payload() -> dict:
    return {
        "external_request_id": "assembler-1",
        "campaign": {"objective": "OUTCOME_TRAFFIC"},
        "adset": {
            "optimization_goal": "LINK_CLICKS",
            "countries": "US,CA",
            "age_min": 18,
            "age_max": 65,
            "genders": "ALL",
            "device_platforms": "ALL",
        },
        "creative": {
            "creative_type": "image",
            "message": "My record: 3 minutes. Can you beat it?",
            "headline": "Beat my record",
            "description": "Try the challenge today.",
            "link": "https://example.com/game",
        },
        "insight": {
            "spend": "0.24",
            "impressions": "1079",
            "inline_link_clicks": "86",
            "actions": [
                {"action_type": "link_click", "value": "86"},
                {"action_type": "landing_page_view", "value": "23"},
            ],
        },
        "siblings": [],
    }


def _assemble(
    *,
    payload: dict | None = None,
    media_analysis: dict | None = None,
    public_research: dict | None = None,
    llm_contribution: dict | None = None,
) -> dict:
    request_payload = deepcopy(payload or _payload())
    return assemble_facebook_ad_analysis(
        request_payload=request_payload,
        rule_analysis=build_facebook_metric_analysis(request_payload),
        media_analysis=media_analysis or {"status": "available"},
        public_research=public_research or {"status": "unavailable"},
        llm_contribution=llm_contribution,
    )


def _valid_result() -> dict:
    return {
        "schema_version": "facebook_ad_analysis_v1",
        "platform": "facebook",
        "summary": "点击进入落地页后的流失较高，先检查页面加载、跳转和事件回传。",
        "overall_decision": {
            "action": "optimize",
            "priority": "high",
            "main_problem": "点击进入落地页后的流失较高",
        },
        "targeting_analysis": [],
        "adjustment_plans": [
            {
                "priority": "high",
                "category": "landing_page",
                "title": "检查点击到落地页的流失",
                "action": "检查移动端页面加载、广告链接跳转和 Meta Pixel 事件回传。",
                "reason": "广告产生86次链接点击，但只有23次落地页浏览。",
                "expected_effect": "减少无效点击花费，提高有效落地页访问量。",
                "what_to_watch": "观察成功进入落地页的人数是否提高。",
            }
        ],
        "copywriting_analysis": {
            "summary": "当前文案具备挑战感。",
            "problems": [],
            "suggestions": [],
            "recommended_primary_text": None,
            "recommended_headline": None,
            "recommended_description": None,
        },
        "media_analysis": {
            "media_type": "image",
            "summary": "素材已完成处理，暂未发现必须修改的明确问题。",
            "improvements": [],
        },
        "market_intelligence": {
            "status": "unavailable",
            "summary": "本次没有找到足够可靠的相似广告参考。",
            "references": [],
            "limitation": "公开来源无法验证真实花费、购买量或 ROAS。",
        },
        "data_gaps": ["缺少购买数据，因此暂时无法判断广告是否带来真实业务转化。"],
    }


def test_result_uses_exact_concise_operator_contract():
    result = _assemble(llm_contribution=None)

    assert set(result) == EXPECTED_TOP_LEVEL_KEYS
    assert len(result["summary"]) <= 100
    assert result["overall_decision"] == {
        "action": "optimize",
        "priority": "high",
        "main_problem": "点击进入落地页后的流失较高",
    }
    assert 1 <= len(result["adjustment_plans"]) <= 5
    FacebookAdAnalysisResult.model_validate(result)


@pytest.mark.parametrize(
    ("mutate", "error_fragment"),
    [
        (lambda value: value.update({"executive_summary": {}}), "extra_forbidden"),
        (lambda value: value.update({"summary": "长" * 101}), "string_too_long"),
        (
            lambda value: value.update(
                {"targeting_analysis": [deepcopy(_targeting_item()) for _ in range(4)]}
            ),
            "too_long",
        ),
        (
            lambda value: value.update({"adjustment_plans": value["adjustment_plans"] * 6}),
            "too_long",
        ),
        (lambda value: value.update({"adjustment_plans": []}), "too_short"),
    ],
)
def test_schema_rejects_old_fields_and_contract_limit_violations(mutate, error_fragment):
    value = _valid_result()
    mutate(value)

    with pytest.raises(ValidationError) as exc_info:
        FacebookAdAnalysisResult.model_validate(value)

    assert error_fragment in str(exc_info.value)


def _targeting_item() -> dict:
    return {
        "dimension": "age",
        "current": "18至65岁",
        "decision": "test",
        "problem": "当前年龄范围较宽，可能导致预算分散。",
        "suggestion": "保留原广告组，复制一个年龄范围更集中的广告组进行小预算测试。",
        "reason": "没有年龄段成效对比，因此不建议直接修改原广告组。",
    }


def test_rule_owned_decision_overrides_conflicting_llm_and_leads_the_plan():
    result = _assemble(
        llm_contribution={
            "summary": "素材很好，可以立即扩量。",
            "overall_decision": {
                "action": "scale",
                "priority": "low",
                "main_problem": "没有问题",
            },
            "adjustment_plans": [
                {
                    "priority": "low",
                    "category": "media",
                    "title": "扩大预算",
                    "action": "立即扩大预算。",
                    "reason": "模型认为素材很好。",
                    "expected_effect": "增加曝光。",
                    "what_to_watch": "观察花费。",
                }
            ],
        }
    )

    assert result["overall_decision"] == {
        "action": "optimize",
        "priority": "high",
        "main_problem": "点击进入落地页后的流失较高",
    }
    assert result["adjustment_plans"][0]["priority"] == "high"
    assert result["adjustment_plans"][0]["category"] == "landing_page"
    assert "落地页" in result["adjustment_plans"][0]["title"]
    assert "先" in result["summary"] or "检查" in result["summary"]


def test_missing_landing_page_events_produces_tracking_plan_without_false_dropoff_claim():
    payload = _payload()
    payload["insight"] = {
        "spend": "0.24",
        "impressions": "1079",
        "inline_link_clicks": "86",
    }

    result = _assemble(payload=payload, llm_contribution=None)

    first_plan = result["adjustment_plans"][0]
    combined = " ".join(str(value) for value in first_plan.values()).lower()
    assert first_plan["category"] == "tracking"
    assert "pixel" in combined or "事件" in combined
    assert "只有23次" not in combined
    assert "流失较高" not in combined
    assert result["overall_decision"]["action"] == "monitor"


def test_targeting_contains_only_meaningful_issues_and_never_invents_dimension_performance():
    result = _assemble(
        llm_contribution={
            "targeting_analysis": [
                {
                    "dimension": "age",
                    "current": "18至65岁",
                    "decision": "adjust",
                    "problem": "18至24岁实际表现最好，应集中预算。",
                    "suggestion": "直接删除其他年龄段。",
                    "reason": "年龄段效果已经得到证明。",
                },
                _targeting_item(),
                {
                    "dimension": "device",
                    "current": "ALL",
                    "decision": "monitor",
                    "problem": "需要继续观察。",
                    "suggestion": "继续观察。",
                    "reason": "暂无明确问题。",
                },
                {
                    "dimension": "country",
                    "current": "US,CA",
                    "decision": "test",
                    "problem": "两个市场共用预算，值得分开验证。",
                    "suggestion": "复制广告组并用小预算分别测试。",
                    "reason": "没有国家成效拆分，不能直接判断哪个国家更好。",
                },
            ]
        }
    )

    assert len(result["targeting_analysis"]) <= 3
    combined = str(result["targeting_analysis"])
    assert "实际表现最好" not in combined
    assert "效果已经得到证明" not in combined
    assert "直接删除其他年龄段" not in combined
    assert all(item["decision"] in {"test", "monitor"} for item in result["targeting_analysis"])
    assert "没有" in combined or "无法" in combined


def test_media_processing_failure_never_exposes_paths_or_invented_visual_observations():
    result = _assemble(
        payload={
            **_payload(),
            "creative": {
                **_payload()["creative"],
                "creative_type": "video",
                "video_url": "https://newpixel.messrocts.com/uploads/ad.mp4",
            },
        },
        media_analysis={
            "status": "unavailable",
            "warnings": ["ffmpeg is unavailable"],
            "local_artifacts": {
                "downloaded_path": "/app/storage/ad-analysis/source.mp4",
                "keyframe_paths": ["/app/storage/ad-analysis/keyframe.jpg"],
            },
        },
        llm_contribution={
            "media_analysis": {
                "media_type": "video",
                "summary": "前三秒出现大量敌人，开场节奏很慢。",
                "improvements": [
                    {
                        "location": "0至3秒",
                        "problem": "敌人出现太晚。",
                        "action": "把敌人提前到第一秒。",
                    }
                ],
            }
        },
    )

    assert result["media_analysis"]["media_type"] == "video"
    assert result["media_analysis"]["improvements"] == []
    assert "未能" in result["media_analysis"]["summary"]
    assert "/app/storage" not in str(result)
    assert any("素材" in item for item in result["data_gaps"])


def test_market_intelligence_is_public_safe_and_limited_to_three_references():
    references = []
    for index in range(4):
        references.append(
            {
                "reference_id": f"ref-{index}",
                "source_type": "public_search",
                "source_url": f"https://example.com/ad-{index}",
                "advertiser_name": f"Advertiser {index}",
                "collected_at": "2026-07-14T00:00:00Z",
                "similarity_score": 0.92,
                "performance_evidence": {
                    "type": "public_proxy_signals",
                    "verified": True,
                    "confidence": "high",
                    "signals": ["currently_active"],
                },
                "creative_patterns": {
                    "public_title": "Survival challenge",
                    "public_excerpt": "The opening immediately shows danger.",
                },
                "applicable_learnings": ["Move the strongest challenge into the opening."],
            }
        )

    result = _assemble(
        public_research={
            "status": "succeeded",
            "selected_reference_ads": references,
            "limitations": ["Public sources cannot verify Meta performance."],
        },
        llm_contribution=None,
    )

    market = result["market_intelligence"]
    assert market["status"] == "completed"
    assert len(market["references"]) == 3
    assert all(
        set(reference) == {"advertiser_name", "source_url", "observed_pattern", "applicable_idea"}
        for reference in market["references"]
    )
    assert "similarity_score" not in str(market)
    assert "performance_evidence" not in str(market)
    assert "collected_at" not in str(market)
    assert "无法验证" in market["limitation"]


def test_copy_recommendations_are_null_when_original_copy_is_missing():
    payload = _payload()
    payload["creative"] = {
        "creative_type": "image",
        "image_url": "https://newpixel.messrocts.com/uploads/ad.jpg",
    }
    result = _assemble(
        payload=payload,
        llm_contribution={
            "copywriting_analysis": {
                "summary": "需要重写。",
                "problems": ["卖点不明确。"],
                "suggestions": ["增加明确优惠。"],
                "recommended_primary_text": "Buy now for 50% off.",
                "recommended_headline": "Guaranteed results",
                "recommended_description": "Limited price offer",
            }
        },
    )

    copywriting = result["copywriting_analysis"]
    assert copywriting["recommended_primary_text"] is None
    assert copywriting["recommended_headline"] is None
    assert copywriting["recommended_description"] is None


def test_llm_failure_still_returns_valid_result_with_at_least_one_plan():
    result = _assemble(
        media_analysis={"status": "unavailable"},
        public_research={"status": "unavailable"},
        llm_contribution=None,
    )

    validated = FacebookAdAnalysisResult.model_validate(result)
    assert validated.adjustment_plans
    assert 1 <= len(result["adjustment_plans"]) <= 5
