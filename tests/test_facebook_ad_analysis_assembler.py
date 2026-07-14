from backend.app.schemas.facebook_ad_analysis import FacebookAdAnalysisResult
from backend.app.services.facebook_ad_analysis_assembler import assemble_facebook_ad_analysis
from backend.app.services.facebook_ad_metrics import build_facebook_metric_analysis


def _payload() -> dict:
    return {
        "external_request_id": "assembler-1",
        "campaign": {"objective": "OUTCOME_TRAFFIC"},
        "adset": {"optimization_goal": "LINK_CLICKS", "countries": "US"},
        "creative": {
            "creative_type": "image",
            "message": "My record: 3 minutes. Can you beat it?",
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


def test_assembler_preserves_rule_owned_fields_over_llm_output():
    rules = build_facebook_metric_analysis(_payload())
    result = assemble_facebook_ad_analysis(
        request_payload=_payload(),
        rule_analysis=rules,
        media_analysis={"status": "available", "source_field": "creative.image_url"},
        public_research={"status": "unavailable", "warnings": ["no provider"]},
        llm_contribution={
            "executive_summary": {
                "verdict": "scale",
                "primary_bottleneck": "creative_hook",
                "pause_recommended": True,
            },
            "creative_analysis": {
                "summary": "首屏挑战感较强。",
                "strengths": ["挑战句式清晰"],
                "weaknesses": ["缺少明确玩法展示"],
            },
            "recommended_actions": [
                {
                    "action_id": "bad_empty",
                    "priority": 1,
                    "category": "creative_hook",
                    "action": "优化素材",
                    "reason": "太泛",
                    "evidence": [],
                    "success_metric": "ctr",
                    "target_direction": "increase",
                }
            ],
        },
    )

    validated = FacebookAdAnalysisResult.model_validate(result)
    assert validated.schema_version == "facebook_ad_analysis_v1"
    assert result["executive_summary"]["verdict"] == "optimize"
    assert result["executive_summary"]["primary_bottleneck"] == "landing_page"
    assert result["executive_summary"]["pause_recommended"] is False
    assert result["creative_analysis"]["summary"] == "首屏挑战感较强。"
    assert all(item["evidence"] for item in result["recommended_actions"])


def test_result_schema_rejects_public_research_verified_performance_claims():
    rules = build_facebook_metric_analysis(_payload())
    result = assemble_facebook_ad_analysis(
        request_payload=_payload(),
        rule_analysis=rules,
        media_analysis={"status": "unavailable"},
        public_research={
            "status": "succeeded",
            "selected_reference_ads": [
                {
                    "reference_id": "ref-1",
                    "source_type": "web_search",
                    "source_url": "https://www.facebook.com/ads/library/?id=123",
                    "advertiser_name": "Example",
                    "collected_at": "2026-07-13T08:00:00Z",
                    "similarity_score": 0.8,
                    "performance_evidence": {
                        "type": "public_proxy_signals",
                        "verified": True,
                        "confidence": "high",
                        "signals": ["currently_active"],
                        "limitations": [],
                    },
                    "creative_patterns": {},
                    "applicable_learnings": [],
                }
            ],
        },
        llm_contribution=None,
    )
    assert (
        result["market_intelligence"]["selected_reference_ads"][0]["performance_evidence"][
            "verified"
        ]
        is False
    )
    FacebookAdAnalysisResult.model_validate(result)


def test_assembler_adapts_provider_string_lists_into_complete_result_sections():
    rules = build_facebook_metric_analysis(_payload())
    result = assemble_facebook_ad_analysis(
        request_payload=_payload(),
        rule_analysis=rules,
        media_analysis={"status": "available"},
        public_research={"status": "succeeded", "selected_reference_ads": []},
        llm_contribution={
            "summary": "Landing-page continuity is the primary issue.",
            "creative_feedback": ["Keep the hook and show more gameplay."],
            "audience_feedback": ["Review delivery by country."],
            "recommended_actions": ["Check page load and pixel events."],
            "next_tests": ["Run a first-frame A/B test."],
        },
    )

    assert result["creative_analysis"]["summary"] == (
        "Landing-page continuity is the primary issue."
    )
    assert result["creative_analysis"]["feedback"] == [
        "Keep the hook and show more gameplay."
    ]
    assert result["audience_and_delivery_analysis"]["feedback"] == [
        "Review delivery by country."
    ]
    assert result["recommended_actions"][0]["action"] == "Check page load and pixel events."
    assert result["recommended_actions"][0]["evidence"]
    assert result["experiment_plan"]["suggested_tests"][0]["name"] == (
        "Run a first-frame A/B test."
    )
    FacebookAdAnalysisResult.model_validate(result)


def test_assembler_never_exposes_private_media_artifact_paths():
    rules = build_facebook_metric_analysis(_payload())
    result = assemble_facebook_ad_analysis(
        request_payload=_payload(),
        rule_analysis=rules,
        media_analysis={
            "status": "available",
            "source_url": "https://newpixel.messrocts.com/uploads/ad.jpg",
            "thumbnail_generated": True,
            "local_artifacts": {
                "downloaded_path": "/app/storage/ad-analysis/ana_1/source.jpg",
                "thumbnail_path": "/app/storage/ad-analysis/ana_1/thumbnail.jpg",
                "keyframe_paths": ["/app/storage/ad-analysis/ana_1/keyframe_2.jpg"],
            },
        },
        public_research={"status": "skipped"},
        llm_contribution=None,
    )

    media = result["creative_analysis"]["media"]
    assert media["status"] == "available"
    assert media["thumbnail_generated"] is True
    assert "local_artifacts" not in media
    assert "/app/storage" not in str(result)


def test_assembler_recommends_event_tracking_verification_when_lpv_is_missing():
    payload = _payload()
    payload["insight"] = {
        "spend": "0.24",
        "impressions": "1079",
        "inline_link_clicks": "86",
    }
    rules = build_facebook_metric_analysis(payload)

    result = assemble_facebook_ad_analysis(
        request_payload=payload,
        rule_analysis=rules,
        media_analysis={"status": "unavailable"},
        public_research={"status": "skipped"},
        llm_contribution=None,
    )

    action = result["recommended_actions"][0]
    combined = " ".join(
        [
            str(action["action"]),
            str(action["reason"]),
            *(str(item.get("claim")) for item in action["evidence"]),
        ]
    ).lower()
    assert "clicks are not converting" not in combined
    assert "landing page view rate is the strongest" not in combined
    assert "pixel" in combined or "event" in combined
    assert action["success_metric"] == "event_tracking_coverage"
    assert action["target_direction"] == "verify"
    FacebookAdAnalysisResult.model_validate(result)
