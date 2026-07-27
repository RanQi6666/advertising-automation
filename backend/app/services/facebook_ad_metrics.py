from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

RULE_ENGINE_VERSION = "facebook_rules_v1"

ACTION_ALIASES = {
    "landing_page_views": ("landing_page_view", "omni_landing_page_view"),
    "purchase": ("purchase", "omni_purchase", "offsite_conversion.fb_pixel_purchase"),
    "add_to_cart": ("add_to_cart", "omni_add_to_cart"),
    "lead": ("lead", "onsite_conversion.lead_grouped", "offsite_conversion.fb_pixel_lead"),
    "complete_registration": (
        "complete_registration",
        "offsite_conversion.fb_pixel_complete_registration",
    ),
    "first_recharge": (
        "first_recharge",
        "offsite_conversion.fb_pixel_first_recharge",
        "offsite_conversion.fb_pixel_custom.first_recharge",
    ),
    "video_plays": ("video_view", "video_play_actions"),
    "video_p25_views": ("video_p25_watched_actions",),
    "video_p50_views": ("video_p50_watched_actions",),
    "video_p75_views": ("video_p75_watched_actions",),
    "video_p95_views": ("video_p95_watched_actions",),
    "video_p100_views": ("video_p100_watched_actions",),
}

DERIVED_RATE_FORMULAS = {
    "landing_page_view_rate": (
        "landing_page_views",
        "inline_link_clicks",
        "landing_page_views / inline_link_clicks * 100",
    ),
    "video_play_rate": ("video_plays", "impressions", "video_plays / impressions * 100"),
    "video_p25_rate": ("video_p25_views", "video_plays", "video_p25_views / video_plays * 100"),
    "video_p50_rate": ("video_p50_views", "video_plays", "video_p50_views / video_plays * 100"),
    "video_p75_rate": ("video_p75_views", "video_plays", "video_p75_views / video_plays * 100"),
    "video_p95_rate": ("video_p95_views", "video_plays", "video_p95_views / video_plays * 100"),
    "video_p100_rate": (
        "video_p100_views",
        "video_plays",
        "video_p100_views / video_plays * 100",
    ),
}

_VIDEO_ACTION_FIELDS = {
    "video_play_actions": "video_play_actions",
    "video_p25_watched_actions": "video_p25_watched_actions",
    "video_p50_watched_actions": "video_p50_watched_actions",
    "video_p75_watched_actions": "video_p75_watched_actions",
    "video_p95_watched_actions": "video_p95_watched_actions",
    "video_p100_watched_actions": "video_p100_watched_actions",
}

_BASE_METRIC_UNITS = {
    "spend": "currency",
    "impressions": "count",
    "reach": "count",
    "frequency": "ratio",
    "clicks": "count",
    "inline_link_clicks": "count",
    "ctr": "percent",
    "inline_link_click_ctr": "percent",
    "cpc": "currency",
    "cpm": "currency",
}

_ALIGNED_OBJECTIVES = {
    ("OUTCOME_TRAFFIC", "LINK_CLICKS"),
    ("OUTCOME_SALES", "OFFSITE_CONVERSIONS"),
    ("OUTCOME_LEADS", "LEAD_GENERATION"),
}


@dataclass(frozen=True)
class MetricDatum:
    value: float | None
    unit: str
    source: str
    assessment: str
    availability: str
    formula: str | None = None
    reason: str | None = None
    currency: str | None = None

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if key == "value" or value is not None}


def build_facebook_metric_analysis(payload: dict[str, Any]) -> dict[str, Any]:
    campaign = _record(payload.get("campaign"))
    adset = _record(payload.get("adset"))
    creative = _record(payload.get("creative"))
    insight = _record(payload.get("insight"))
    currency = _currency(payload, insight)
    action_counts = _collect_action_counts(insight)

    metrics = _base_metrics(insight, currency)
    metrics.update(_action_metrics(action_counts))
    metrics.update(_video_action_metrics(action_counts))
    metrics.update(_derived_metrics(metrics))
    metrics.update(_business_efficiency_metrics(metrics, currency))

    objective_alignment = _objective_alignment(payload, campaign, adset, currency)
    diagnoses, warnings = _diagnoses(metrics, insight)
    benchmark_comparison = _benchmark_comparison(payload.get("siblings"))
    data_quality, meaningful = _data_quality(metrics)
    performance_funnel = _performance_funnel(metrics, creative)
    executive_summary = _executive_summary(metrics, diagnoses, data_quality)

    return {
        "schema_version": RULE_ENGINE_VERSION,
        "platform": "facebook",
        "metrics": metrics,
        "objective_alignment": objective_alignment,
        "performance_funnel": performance_funnel,
        "diagnoses": diagnoses,
        "executive_summary": executive_summary,
        "benchmark_comparison": benchmark_comparison,
        "data_quality": data_quality,
        "warnings": warnings,
        "meaningful": meaningful,
        "analysis_metadata": {"rule_engine_version": RULE_ENGINE_VERSION},
    }


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _currency(payload: dict[str, Any], insight: dict[str, Any]) -> str:
    return "USD"


def _base_metrics(insight: dict[str, Any], currency: str | None) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for name, unit in _BASE_METRIC_UNITS.items():
        value = _decimal_from(insight.get(name))
        if value is None:
            metric = MetricDatum(
                value=None,
                unit=unit,
                source="missing",
                assessment="unavailable",
                availability="unavailable",
                reason=f"{name} was not supplied",
                currency=currency if unit == "currency" else None,
            )
        else:
            number = _to_float(value)
            metric = MetricDatum(
                value=number,
                unit=unit,
                source="meta_insight",
                assessment="zero" if number == 0 else "available",
                availability="available",
                currency=currency if unit == "currency" else None,
            )
        metric_dict = metric.as_dict()
        if unit == "currency":
            metric_dict["currency"] = currency
        metrics[name] = metric_dict
    return metrics


def _action_metrics(action_values: dict[str, Decimal]) -> dict[str, dict[str, Any]]:
    return {
        "landing_page_views": _action_metric(
            action_values,
            "landing_page_views",
            unit="count",
            missing_reason="landing_page_view was not supplied",
        ),
        "purchase": _action_metric(
            action_values,
            "purchase",
            unit="count",
            missing_reason="purchase was not supplied",
        ),
        "add_to_cart": _action_metric(
            action_values,
            "add_to_cart",
            unit="count",
            missing_reason="add_to_cart was not supplied",
        ),
        "lead": _action_metric(
            action_values,
            "lead",
            unit="count",
            missing_reason="lead was not supplied",
        ),
        "complete_registration": _action_metric(
            action_values,
            "complete_registration",
            unit="count",
            missing_reason="complete_registration was not supplied",
        ),
        "first_recharge": _action_metric(
            action_values,
            "first_recharge",
            unit="count",
            missing_reason="first_recharge was not supplied",
        ),
    }


def _video_action_metrics(action_values: dict[str, Decimal]) -> dict[str, dict[str, Any]]:
    return {
        name: _action_metric(
            action_values,
            name,
            unit="count",
            missing_reason=f"{name} was not supplied",
        )
        for name in (
            "video_plays",
            "video_p25_views",
            "video_p50_views",
            "video_p75_views",
            "video_p95_views",
            "video_p100_views",
        )
    }


def _action_metric(
    action_values: dict[str, Decimal],
    metric_name: str,
    *,
    unit: str,
    missing_reason: str,
) -> dict[str, Any]:
    value = _first_action_value(action_values, ACTION_ALIASES[metric_name])
    if value is None:
        return MetricDatum(
            value=None,
            unit=unit,
            source="missing",
            assessment="unavailable",
            availability="unavailable",
            reason=missing_reason,
        ).as_dict()
    number = _to_float(value)
    return MetricDatum(
        value=number,
        unit=unit,
        source="meta_actions",
        assessment="zero" if number == 0 else "available",
        availability="available",
    ).as_dict()


def _derived_metrics(metrics: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    derived: dict[str, dict[str, Any]] = {}
    for name, (numerator_name, denominator_name, formula) in DERIVED_RATE_FORMULAS.items():
        numerator = _metric_value(metrics.get(numerator_name))
        denominator = _metric_value(metrics.get(denominator_name))
        if numerator is None or denominator is None or denominator == 0:
            derived[name] = MetricDatum(
                value=None,
                unit="percent",
                source="calculated",
                assessment="unavailable",
                availability="unavailable",
                formula=formula,
                reason=f"{denominator_name} is zero or missing",
            ).as_dict()
            continue
        value = numerator / denominator * 100
        derived[name] = MetricDatum(
            value=_to_float(value),
            unit="percent",
            source="calculated",
            assessment=_rate_assessment(name, value),
            availability="available",
            formula=formula,
        ).as_dict()
    return derived


def _business_efficiency_metrics(
    metrics: dict[str, dict[str, Any]], currency: str | None
) -> dict[str, dict[str, Any]]:
    return {
        "cost_per_purchase": _cost_per_action_metric(
            metrics,
            action_name="purchase",
            formula="spend / purchase",
            currency=currency,
        ),
        "cost_per_add_to_cart": _cost_per_action_metric(
            metrics,
            action_name="add_to_cart",
            formula="spend / add_to_cart",
            currency=currency,
        ),
        "cost_per_lead": _cost_per_action_metric(
            metrics,
            action_name="lead",
            formula="spend / lead",
            currency=currency,
        ),
        "cost_per_complete_registration": _cost_per_action_metric(
            metrics,
            action_name="complete_registration",
            formula="spend / complete_registration",
            currency=currency,
        ),
        "cost_per_first_recharge": _cost_per_action_metric(
            metrics,
            action_name="first_recharge",
            formula="spend / first_recharge",
            currency=currency,
        ),
    }


def _cost_per_action_metric(
    metrics: dict[str, dict[str, Any]],
    *,
    action_name: str,
    formula: str,
    currency: str | None,
) -> dict[str, Any]:
    spend = _metric_value(metrics.get("spend"))
    action_count = _metric_value(metrics.get(action_name))
    if spend is None or action_count is None or action_count == 0:
        return MetricDatum(
            value=None,
            unit="currency",
            source="calculated",
            assessment="unavailable",
            availability="unavailable",
            formula=formula,
            reason=f"spend or {action_name} is zero or missing",
            currency=currency,
        ).as_dict()
    return MetricDatum(
        value=_to_float(spend / action_count),
        unit="currency",
        source="calculated",
        assessment="available",
        availability="available",
        formula=formula,
        currency=currency,
    ).as_dict()


def _collect_action_counts(insight: dict[str, Any]) -> dict[str, Decimal]:
    values: dict[str, Decimal] = {}
    _add_action_rows(values, insight.get("actions"))
    for field_name, canonical_name in _VIDEO_ACTION_FIELDS.items():
        field_value = _sum_action_rows(insight.get(field_name))
        if field_value is not None:
            values[canonical_name] = field_value
    return values


def _add_action_rows(values: dict[str, Decimal], rows: Any) -> None:
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        action_type = row.get("action_type")
        if action_type is None:
            continue
        value = _decimal_from(row.get("value"))
        if value is None:
            continue
        key = str(action_type)
        values[key] = values.get(key, Decimal("0")) + value


def _sum_action_rows(rows: Any) -> Decimal | None:
    if not isinstance(rows, list):
        return None
    total: Decimal | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        value = _decimal_from(row.get("value"))
        if value is None:
            continue
        total = value if total is None else total + value
    return total


def _first_action_value(
    action_values: dict[str, Decimal], aliases: tuple[str, ...]
) -> Decimal | None:
    for alias in aliases:
        if alias in action_values:
            return action_values[alias]
    return None


def _rate_assessment(name: str, value: Decimal) -> str:
    if name == "landing_page_view_rate":
        if value < Decimal("35"):
            return "critical"
        if value < Decimal("50"):
            return "weak"
        return "healthy"
    if value == 0:
        return "zero"
    return "available"


def _objective_alignment(
    payload: dict[str, Any],
    campaign: dict[str, Any],
    adset: dict[str, Any],
    currency: str | None,
) -> dict[str, Any]:
    objective = str(campaign.get("objective") or "").strip().upper()
    optimization_goal = str(adset.get("optimization_goal") or "").strip().upper()
    business_goal = _infer_business_goal(adset, _record(payload.get("insight")))
    return {
        "objective": objective or None,
        "optimization_goal": optimization_goal or None,
        "meta_configuration": (
            "aligned" if (objective, optimization_goal) in _ALIGNED_OBJECTIVES else "unknown"
        ),
        "business_goal": business_goal or "unknown",
        "currency": currency,
    }


def _infer_business_goal(adset: dict[str, Any], insight: dict[str, Any]) -> str:
    promoted_object = _record(adset.get("promoted_object"))
    configured_event = (
        adset.get("custom_event_type")
        or promoted_object.get("custom_event_type")
    )
    configured_goal = _business_goal_from_event(configured_event)
    if configured_goal:
        return configured_goal

    action_counts = _collect_action_counts(insight)
    for goal in ("purchase", "first_recharge", "complete_registration", "lead"):
        if _first_action_value(action_counts, ACTION_ALIASES[goal]) is not None:
            return goal
    return "unknown"


def _business_goal_from_event(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace("-", "_")
    if not normalized:
        return None
    for goal in ("purchase", "first_recharge", "complete_registration", "lead"):
        if normalized in ACTION_ALIASES[goal] or normalized == goal:
            return goal
    return None


def _diagnoses(
    metrics: dict[str, dict[str, Any]], insight: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[str]]:
    diagnoses: list[dict[str, Any]] = []
    warnings: list[str] = []
    inline_clicks = _metric_value(metrics.get("inline_link_clicks"))
    landing_page_rate = _metric_value(metrics.get("landing_page_view_rate"))
    if (
        inline_clicks is not None
        and inline_clicks >= Decimal("20")
        and landing_page_rate is not None
        and landing_page_rate < Decimal("35")
    ):
        diagnoses.append(
            {
                "diagnosis_id": "landing_page_dropoff_v1",
                "category": "landing_page",
                "severity": "high",
                "confidence": "medium",
                "title": "Landing page view rate is critically low after link clicks",
                "conclusion": (
                    "The ad earns clicks, but too few clickers reach a landing page view."
                ),
                "evidence": [
                    {
                        "metric": "landing_page_view_rate",
                        "value": _to_float(landing_page_rate),
                        "threshold": "below 35%",
                    },
                    {"metric": "inline_link_clicks", "value": _to_float(inline_clicks)},
                ],
                "possible_causes": [
                    "Check page loading speed after the ad click.",
                    "Check redirect, tracking, or deep-link reliability.",
                    "Check message-to-page expectation match.",
                ],
            }
        )

    frequency = _decimal_from(insight.get("frequency"))
    if frequency is not None and frequency >= Decimal("3"):
        warnings.append(
            "Frequency alone is not enough to prove creative fatigue without "
            "time-series or sibling evidence."
        )
    return diagnoses, warnings


def _benchmark_comparison(siblings: Any) -> dict[str, Any]:
    sibling_rows = siblings if isinstance(siblings, list) else []
    total_impressions = Decimal("0")
    for sibling in sibling_rows:
        if not isinstance(sibling, dict):
            continue
        insight = _record(sibling.get("insight")) or sibling
        value = _decimal_from(insight.get("impressions"))
        if value is not None:
            total_impressions += value
    if len(sibling_rows) < 2 or total_impressions < Decimal("100"):
        return {
            "submitted_siblings": {
                "status": "insufficient_data",
                "sibling_count": len(sibling_rows),
                "total_impressions": _to_float(total_impressions),
                "reason": "requires at least two sibling ads and 100 total sibling impressions",
            }
        }
    return {
        "submitted_siblings": {
            "status": "available",
            "sibling_count": len(sibling_rows),
            "total_impressions": _to_float(total_impressions),
        }
    }


def _data_quality(metrics: dict[str, dict[str, Any]]) -> tuple[dict[str, Any], bool]:
    missing_metrics = [
        name
        for name in (
            "purchase",
            "add_to_cart",
            "lead",
            "complete_registration",
            "first_recharge",
        )
        if metrics[name]["source"] == "missing"
    ]
    impressions = _metric_value(metrics.get("impressions"))
    spend = _metric_value(metrics.get("spend"))
    meaningful = (
        impressions is not None
        and impressions >= Decimal("100")
        and spend is not None
        and spend > 0
    )
    return (
        {
            "sample_size": {
                "impressions": _to_float(impressions) if impressions is not None else None,
                "spend": _to_float(spend) if spend is not None else None,
            },
            "missing_metrics": missing_metrics,
            "meaningful_for_directional_analysis": meaningful,
        },
        meaningful,
    )


def _performance_funnel(
    metrics: dict[str, dict[str, Any]], creative: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    return {
        "delivery": {
            "metrics": {
                "spend": metrics["spend"],
                "impressions": metrics["impressions"],
                "reach": metrics["reach"],
                "frequency": metrics["frequency"],
                "cpm": metrics["cpm"],
            }
        },
        "click": {
            "metrics": {
                "clicks": metrics["clicks"],
                "inline_link_clicks": metrics["inline_link_clicks"],
                "ctr": metrics["ctr"],
                "inline_link_click_ctr": metrics["inline_link_click_ctr"],
                "cpc": metrics["cpc"],
            }
        },
        "landing_page": {
            "metrics": {
                "landing_page_views": metrics["landing_page_views"],
                "landing_page_view_rate": metrics["landing_page_view_rate"],
            }
        },
        "conversion": {
            "metrics": {
                "purchase": metrics["purchase"],
                "cost_per_purchase": metrics["cost_per_purchase"],
                "add_to_cart": metrics["add_to_cart"],
                "cost_per_add_to_cart": metrics["cost_per_add_to_cart"],
                "lead": metrics["lead"],
                "cost_per_lead": metrics["cost_per_lead"],
                "complete_registration": metrics["complete_registration"],
                "cost_per_complete_registration": metrics[
                    "cost_per_complete_registration"
                ],
                "first_recharge": metrics["first_recharge"],
                "cost_per_first_recharge": metrics["cost_per_first_recharge"],
            }
        },
        "video": {
            "creative_type": str(creative.get("creative_type") or "unknown").lower(),
            "metrics": {
                "video_plays": metrics["video_plays"],
                "video_play_rate": metrics["video_play_rate"],
                "video_p25_rate": metrics["video_p25_rate"],
                "video_p50_rate": metrics["video_p50_rate"],
                "video_p75_rate": metrics["video_p75_rate"],
                "video_p95_rate": metrics["video_p95_rate"],
                "video_p100_rate": metrics["video_p100_rate"],
            },
        },
    }


def _executive_summary(
    metrics: dict[str, dict[str, Any]],
    diagnoses: list[dict[str, Any]],
    data_quality: dict[str, Any],
) -> dict[str, Any]:
    impressions = _metric_value(metrics.get("impressions"))
    spend = _metric_value(metrics.get("spend"))
    landing_page_rate = _metric_value(metrics.get("landing_page_view_rate"))
    has_landing_page_bottleneck = any(item["category"] == "landing_page" for item in diagnoses)
    purchase_missing = metrics["purchase"]["source"] == "missing"

    confidence = "medium"
    if impressions is None or impressions < Decimal("100") or spend is None or spend == 0:
        confidence = "low"

    if has_landing_page_bottleneck:
        verdict = "optimize"
        primary_bottleneck = "landing_page"
        priority = "high"
    else:
        verdict = "monitor"
        primary_bottleneck = "none"
        priority = "medium" if data_quality["meaningful_for_directional_analysis"] else "low"

    scale_eligibility = "not_ready"
    if (
        not has_landing_page_bottleneck
        and not purchase_missing
        and data_quality["meaningful_for_directional_analysis"]
    ):
        scale_eligibility = "review"

    key_findings: list[str] = []
    if landing_page_rate is not None:
        key_findings.append(f"Landing page view rate is {_to_float(landing_page_rate):.2f}%.")
    if purchase_missing:
        key_findings.append(
            "Purchase event data is unavailable, so cost per purchase cannot be assessed."
        )
    if not key_findings:
        key_findings.append(
            "Submitted data is not yet sufficient for a strong deterministic conclusion."
        )

    return {
        "verdict": verdict,
        "priority": priority,
        "primary_bottleneck": primary_bottleneck,
        "confidence": confidence,
        "scale_eligibility": scale_eligibility,
        "pause_recommended": False,
        "key_findings": key_findings,
    }


def _metric_value(metric: dict[str, Any] | None) -> Decimal | None:
    if not metric:
        return None
    return _decimal_from(metric.get("value"))


def _decimal_from(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        decimal_value = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not decimal_value.is_finite():
        return None
    return decimal_value


def _to_float(value: Decimal) -> float:
    return float(value)
