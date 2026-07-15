import pytest

from backend.app.services.facebook_ad_metrics import build_facebook_metric_analysis


def _sample_payload() -> dict:
    return {
        "external_request_id": "metrics-1",
        "campaign": {"objective": "OUTCOME_TRAFFIC", "name": "new1"},
        "adset": {"optimization_goal": "LINK_CLICKS", "countries": "US"},
        "creative": {"creative_type": "image", "message": "My record: 3 minutes. Can you beat it?"},
        "insight": {
            "spend": "0.24",
            "impressions": "1079",
            "reach": "937",
            "frequency": "1.151547",
            "clicks": "83",
            "inline_link_clicks": "86",
            "ctr": "7.692308",
            "inline_link_click_ctr": "7.970343",
            "cpc": "0.002892",
            "cpm": "0.222428",
            "actions": [
                {"action_type": "link_click", "value": "86"},
                {"action_type": "landing_page_view", "value": "23"},
            ],
        },
        "siblings": [{"insight": {"impressions": "19"}}],
    }


def test_sample_rules_return_approved_topline_and_landing_page_rate():
    analysis = build_facebook_metric_analysis(_sample_payload())

    assert analysis["executive_summary"]["verdict"] == "optimize"
    assert analysis["executive_summary"]["primary_bottleneck"] == "landing_page"
    assert analysis["executive_summary"]["confidence"] == "medium"
    assert analysis["executive_summary"]["scale_eligibility"] == "not_ready"
    assert analysis["executive_summary"]["pause_recommended"] is False
    metric = analysis["performance_funnel"]["landing_page"]["metrics"]["landing_page_view_rate"]
    assert metric["value"] == pytest.approx(26.744186, abs=0.001)
    assert metric["source"] == "calculated"
    assert metric["formula"] == "landing_page_views / inline_link_clicks * 100"
    assert analysis["performance_funnel"]["conversion"]["metrics"]["purchase"]["value"] is None
    assert (
        analysis["performance_funnel"]["conversion"]["metrics"]["purchase"]["availability"]
        == "unavailable"
    )


def test_currency_is_fixed_to_usd():
    payload = _sample_payload()
    payload["insight"]["currency"] = "INR"
    analysis = build_facebook_metric_analysis(payload)
    assert analysis["objective_alignment"]["currency"] == "USD"


def test_business_goal_is_inferred_from_adset_custom_event_type():
    payload = _sample_payload()
    payload["adset"]["custom_event_type"] = "purchase"

    analysis = build_facebook_metric_analysis(payload)

    assert analysis["objective_alignment"]["business_goal"] == "purchase"


def test_business_goal_is_inferred_from_normalized_uppercase_custom_event_type():
    payload = _sample_payload()
    payload["adset"]["custom_event_type"] = "COMPLETE_REGISTRATION"

    analysis = build_facebook_metric_analysis(payload)

    assert analysis["objective_alignment"]["business_goal"] == "complete_registration"


def test_business_goal_does_not_read_legacy_camel_case_event_key():
    payload = _sample_payload()
    payload["adset"] = {"customEventType": "purchase"}
    payload["insight"]["actions"] = []

    analysis = build_facebook_metric_analysis(payload)

    assert analysis["objective_alignment"]["business_goal"] == "unknown"


def test_sales_payload_distinguishes_zero_purchase_from_missing_purchase():
    payload = _sample_payload()
    payload["campaign"]["objective"] = "OUTCOME_SALES"
    payload["adset"]["optimization_goal"] = "OFFSITE_CONVERSIONS"
    payload["insight"]["actions"].append({"action_type": "purchase", "value": "0"})
    analysis = build_facebook_metric_analysis(payload)
    purchase = analysis["performance_funnel"]["conversion"]["metrics"]["purchase"]
    assert purchase["value"] == 0
    assert purchase["availability"] == "available"
    assert purchase["source"] == "meta_actions"


def test_conversion_cost_metrics_do_not_depend_on_action_values_or_roas():
    payload = _sample_payload()
    payload["campaign"]["objective"] = "OUTCOME_SALES"
    payload["adset"]["optimization_goal"] = "OFFSITE_CONVERSIONS"
    payload["insight"]["spend"] = "100"
    payload["insight"]["actions"].extend(
        [
            {"action_type": "purchase", "value": "5"},
            {"action_type": "first_recharge", "value": "2"},
        ]
    )
    analysis = build_facebook_metric_analysis(payload)
    conversion = analysis["performance_funnel"]["conversion"]["metrics"]

    assert conversion["purchase"]["value"] == 5
    assert conversion["first_recharge"]["value"] == 2
    assert "purchase_value" not in conversion
    assert "first_recharge_value" not in conversion
    assert "purchase_roas" not in conversion
    assert "first_recharge_roas" not in conversion
    assert conversion["cost_per_purchase"]["value"] == pytest.approx(20)
    assert conversion["cost_per_first_recharge"]["value"] == pytest.approx(50)
    assert "purchase_value" not in analysis["data_quality"]["missing_metrics"]
    assert "first_recharge_value" not in analysis["data_quality"]["missing_metrics"]


def test_missing_purchase_event_does_not_claim_missing_revenue_or_roas():
    analysis = build_facebook_metric_analysis(_sample_payload())

    assert analysis["executive_summary"]["key_findings"] == [
        "Landing page view rate is 26.74%.",
        "Purchase event data is unavailable, so cost per purchase cannot be assessed.",
    ]
