import pytest

from backend.app.services.facebook_ad_metrics import build_facebook_metric_analysis


def _sample_payload() -> dict:
    return {
        "external_request_id": "metrics-1",
        "account_currency": "USD",
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
            "cost_per_action_type": [{"action_type": "landing_page_view", "value": "0.010435"}],
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


def test_missing_currency_does_not_default_to_usd():
    payload = _sample_payload()
    payload.pop("account_currency")
    payload["insight"].pop("currency", None)
    analysis = build_facebook_metric_analysis(payload)
    assert analysis["objective_alignment"]["currency"] is None


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


def test_action_values_drive_revenue_roas_and_cost_metrics():
    payload = _sample_payload()
    payload["account_currency"] = "USD"
    payload["campaign"]["objective"] = "OUTCOME_SALES"
    payload["adset"]["optimization_goal"] = "OFFSITE_CONVERSIONS"
    payload["insight"]["spend"] = "100"
    payload["insight"]["actions"].extend(
        [
            {"action_type": "purchase", "value": "5"},
            {"action_type": "first_recharge", "value": "2"},
        ]
    )
    payload["insight"]["action_values"] = [
        {"action_type": "purchase", "value": "250.00"},
        {"action_type": "first_recharge", "value": "120.00"},
    ]

    analysis = build_facebook_metric_analysis(payload)
    conversion = analysis["performance_funnel"]["conversion"]["metrics"]

    assert conversion["purchase"]["value"] == 5
    assert conversion["first_recharge"]["value"] == 2
    assert conversion["purchase_value"]["value"] == 250
    assert conversion["purchase_value"]["unit"] == "currency"
    assert conversion["purchase_value"]["currency"] == "USD"
    assert conversion["first_recharge_value"]["value"] == 120
    assert conversion["purchase_roas"]["value"] == pytest.approx(2.5)
    assert conversion["purchase_roas"]["formula"] == "purchase_value / spend"
    assert conversion["cost_per_purchase"]["value"] == pytest.approx(20)
    assert conversion["cost_per_first_recharge"]["value"] == pytest.approx(50)
