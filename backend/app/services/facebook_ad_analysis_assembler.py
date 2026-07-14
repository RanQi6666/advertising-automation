from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from backend.app.schemas.facebook_ad_analysis import FacebookAdAnalysisResult
from backend.app.services.ad_analysis_media_service import public_media_summary

RULE_RESULT_SCHEMA_VERSION = "facebook_ad_analysis_v1"


def assemble_facebook_ad_analysis(
    *,
    request_payload: dict[str, Any],
    rule_analysis: dict[str, Any],
    media_analysis: dict[str, Any] | None,
    public_research: dict[str, Any] | None,
    llm_contribution: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge deterministic Facebook facts with optional inferred sections.

    Rule-owned facts are copied last/authoritatively for the fields that define
    business verdicts and metric provenance. LLM and research subsystems can add
    narrative and recommendations, but cannot rewrite the Meta-derived truth.
    """

    request_payload = _dict(request_payload)
    rule_analysis = _dict(rule_analysis)
    media_analysis = _dict(media_analysis)
    public_research = _dict(public_research)
    llm_contribution = _dict(llm_contribution)

    result = {
        "schema_version": RULE_RESULT_SCHEMA_VERSION,
        "platform": "facebook",
        "executive_summary": deepcopy(rule_analysis.get("executive_summary") or {}),
        "objective_alignment": deepcopy(rule_analysis.get("objective_alignment") or {}),
        "performance_funnel": deepcopy(rule_analysis.get("performance_funnel") or {}),
        "diagnoses": deepcopy(rule_analysis.get("diagnoses") or []),
        "creative_analysis": _creative_analysis(request_payload, media_analysis, llm_contribution),
        "audience_and_delivery_analysis": _audience_and_delivery_analysis(
            request_payload,
            rule_analysis,
            llm_contribution,
        ),
        "market_intelligence": _market_intelligence(public_research),
        "benchmark_comparison": deepcopy(rule_analysis.get("benchmark_comparison") or {}),
        "recommended_actions": _recommended_actions(rule_analysis, llm_contribution),
        "experiment_plan": _experiment_plan(rule_analysis, llm_contribution),
        "data_quality": deepcopy(rule_analysis.get("data_quality") or {}),
        "analysis_metadata": _analysis_metadata(rule_analysis, media_analysis, public_research),
    }

    # Validation is intentionally part of assembly: callers should only persist a
    # public result that conforms to the strict external contract.
    return FacebookAdAnalysisResult.model_validate(result).model_dump(mode="json")


def _creative_analysis(
    request_payload: dict[str, Any],
    media_analysis: dict[str, Any],
    llm_contribution: dict[str, Any],
) -> dict[str, Any]:
    llm_creative = _dict(llm_contribution.get("creative_analysis"))
    creative = _dict(request_payload.get("creative"))
    creative_feedback = _list(llm_contribution.get("creative_feedback"))
    return {
        "summary": _text_or_default(
            llm_creative.get("summary") or llm_contribution.get("summary"),
            "Creative analysis is based on submitted copy plus any internally "
            "processed media artifacts.",
        ),
        "strengths": _list(llm_creative.get("strengths")),
        "weaknesses": _list(llm_creative.get("weaknesses")),
        "feedback": creative_feedback,
        "visual_analysis": _dict(llm_contribution.get("visual_analysis")) or None,
        "creative_type": str(creative.get("creative_type") or "unknown").lower(),
        "message": creative.get("message"),
        "media": public_media_summary(media_analysis) or {"status": "unavailable"},
    }


def _audience_and_delivery_analysis(
    request_payload: dict[str, Any],
    rule_analysis: dict[str, Any],
    llm_contribution: dict[str, Any],
) -> dict[str, Any]:
    llm_section = _dict(llm_contribution.get("audience_and_delivery_analysis"))
    audience_feedback = _list(llm_contribution.get("audience_feedback"))
    adset = _dict(request_payload.get("adset"))
    return {
        "summary": _text_or_default(
            llm_section.get("summary"),
            "Audience and delivery analysis is directional because only submitted "
            "Meta fields are available.",
        ),
        "countries": adset.get("countries"),
        "optimization_goal": adset.get("optimization_goal"),
        "objective_alignment": deepcopy(rule_analysis.get("objective_alignment") or {}),
        "risks": _list(llm_section.get("risks")),
        "opportunities": _list(llm_section.get("opportunities")),
        "feedback": audience_feedback,
        "landing_page_feedback": _list(llm_contribution.get("landing_page_feedback")),
        "budget_delivery_feedback": _list(
            llm_contribution.get("budget_delivery_feedback")
        ),
    }


def _market_intelligence(public_research: dict[str, Any]) -> dict[str, Any]:
    status = str(public_research.get("status") or "skipped")
    references = []
    for index, reference in enumerate(
        _list(public_research.get("selected_reference_ads")), start=1
    ):
        if not isinstance(reference, dict):
            continue
        item = deepcopy(reference)
        item.setdefault("reference_id", f"reference-{index}")
        item.setdefault("source_type", "public_source")
        item.setdefault("source_url", "about:blank")
        item.setdefault("collected_at", _iso_now())
        item.setdefault("similarity_score", 0.0)
        evidence = _dict(item.get("performance_evidence"))
        evidence.setdefault("type", "public_proxy_signals")
        evidence["verified"] = False
        evidence.setdefault("confidence", "unknown")
        evidence.setdefault("signals", [])
        limitations = _list(evidence.get("limitations"))
        if not limitations:
            limitations = [
                "Public sources are creative/proxy references only; Meta performance "
                "metrics are not verified."
            ]
        evidence["limitations"] = limitations
        item["performance_evidence"] = evidence
        item.setdefault("creative_patterns", {})
        item.setdefault("applicable_learnings", [])
        references.append(item)

    warnings = _list(public_research.get("warnings"))
    limitations = _list(public_research.get("limitations"))
    if status in {"skipped", "disabled", "unavailable"} and not limitations:
        limitations.append(
            "Public research is disabled or unavailable, so no live similar-ad "
            "references were used."
        )
    return {
        "status": "skipped" if status == "disabled" else status,
        "selected_reference_ads": references,
        "warnings": warnings,
        "limitations": limitations,
    }


def _recommended_actions(
    rule_analysis: dict[str, Any],
    llm_contribution: dict[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for index, item in enumerate(_list(llm_contribution.get("recommended_actions")), start=1):
        if isinstance(item, str):
            action = {"action": item.strip()}
        elif isinstance(item, dict):
            action = deepcopy(item)
        else:
            continue
        if not action.get("action"):
            continue
        action.setdefault("action_id", f"llm_action_{index}")
        action.setdefault("priority", index)
        action.setdefault("category", _primary_bottleneck(rule_analysis))
        action.setdefault("action", "Review and optimize the identified bottleneck.")
        action.setdefault("reason", "Recommended from submitted ad-performance context.")
        action.setdefault("success_metric", _success_metric(rule_analysis))
        action.setdefault("target_direction", _target_direction(action["success_metric"]))
        if not _list(action.get("evidence")):
            action["evidence"] = _fallback_evidence(rule_analysis)
        actions.append(action)

    if not actions:
        if _has_landing_page_evidence(rule_analysis):
            fallback = {
                "action_id": "fix_landing_page_dropoff_v1",
                "priority": 1,
                "category": _primary_bottleneck(rule_analysis),
                "action": (
                    "Verify landing-page load, redirect, pixel, and message-to-page "
                    "consistency before scaling."
                ),
                "reason": (
                    "Submitted Meta data shows clicks are not converting into landing "
                    "page views at a healthy rate."
                ),
                "evidence": _fallback_evidence(rule_analysis),
                "success_metric": _success_metric(rule_analysis),
                "target_direction": "increase",
            }
        else:
            fallback = {
                "action_id": "verify_event_tracking_coverage_v1",
                "priority": 1,
                "category": "measurement",
                "action": (
                    "Verify Meta Pixel/CAPI event coverage and submit landing_page_view, "
                    "purchase, and action_values data before diagnosing the conversion funnel."
                ),
                "reason": (
                    "The submitted event data is insufficient to confirm whether the landing "
                    "page or downstream conversion stages are the primary bottleneck."
                ),
                "evidence": _fallback_evidence(rule_analysis),
                "success_metric": "event_tracking_coverage",
                "target_direction": "verify",
            }
        actions.append(fallback)
    return actions


def _experiment_plan(
    rule_analysis: dict[str, Any],
    llm_contribution: dict[str, Any],
) -> dict[str, Any]:
    llm_plan = _dict(llm_contribution.get("experiment_plan"))
    suggested_tests = _normalize_suggested_tests(llm_contribution.get("next_tests"))
    return {
        "summary": _text_or_default(
            llm_plan.get("summary"),
            "Run a controlled optimization test against the primary bottleneck before "
            "increasing budget.",
        ),
        "primary_metric": llm_plan.get("primary_metric") or _success_metric(rule_analysis),
        "guardrail_metrics": _list(llm_plan.get("guardrail_metrics"))
        or ["spend", "inline_link_clicks"],
        "suggested_tests": _list(llm_plan.get("suggested_tests"))
        or suggested_tests
        or [
            {
                "name": "Landing-page continuity check",
                "hypothesis": (
                    "Reducing redirect/load friction will increase landing page view rate."
                ),
            }
        ],
    }


def _normalize_suggested_tests(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in _list(value):
        if isinstance(item, str) and item.strip():
            result.append({"name": item.strip(), "hypothesis": item.strip()})
        elif isinstance(item, dict):
            result.append(deepcopy(item))
    return result


def _analysis_metadata(
    rule_analysis: dict[str, Any],
    media_analysis: dict[str, Any],
    public_research: dict[str, Any],
) -> dict[str, Any]:
    warnings = []
    warnings.extend(_list(rule_analysis.get("warnings")))
    warnings.extend(_list(media_analysis.get("warnings")))
    warnings.extend(_list(public_research.get("warnings")))
    if str(public_research.get("status") or "skipped") in {"skipped", "disabled", "unavailable"}:
        warnings.append(
            "Public research is disabled or unavailable; similar-ad intelligence is degraded."
        )
    return {
        **_dict(rule_analysis.get("analysis_metadata")),
        "result_schema_version": RULE_RESULT_SCHEMA_VERSION,
        "assembled_at": _iso_now(),
        "warnings": _dedupe_text(warnings),
    }


def _fallback_evidence(rule_analysis: dict[str, Any]) -> list[dict[str, Any]]:
    funnel = _dict(rule_analysis.get("performance_funnel"))
    landing_page = _dict(funnel.get("landing_page"))
    metrics = _dict(landing_page.get("metrics"))
    lpv_rate = _dict(metrics.get("landing_page_view_rate"))
    evidence = {
        "source": "meta_rules",
        "claim": (
            "Landing page view rate is the strongest deterministic bottleneck in the "
            "submitted data."
        ),
        "metric": "landing_page_view_rate",
        "value": lpv_rate.get("value"),
        "formula": lpv_rate.get("formula"),
    }
    if evidence["value"] is None:
        evidence = {
            "source": "meta_rules",
            "claim": (
                "Landing-page and downstream conversion event coverage is incomplete, so the "
                "conversion bottleneck cannot yet be verified."
            ),
            "metric": "event_tracking_coverage",
            "value": None,
            "formula": None,
        }
    return [evidence]


def _has_landing_page_evidence(rule_analysis: dict[str, Any]) -> bool:
    funnel = _dict(rule_analysis.get("performance_funnel"))
    landing_page = _dict(funnel.get("landing_page"))
    metrics = _dict(landing_page.get("metrics"))
    lpv_rate = _dict(metrics.get("landing_page_view_rate"))
    return lpv_rate.get("value") is not None or _primary_bottleneck(rule_analysis) == "landing_page"


def _primary_bottleneck(rule_analysis: dict[str, Any]) -> str:
    summary = _dict(rule_analysis.get("executive_summary"))
    value = str(summary.get("primary_bottleneck") or "performance").strip()
    return value or "performance"


def _success_metric(rule_analysis: dict[str, Any]) -> str:
    if _primary_bottleneck(rule_analysis) == "landing_page":
        return "landing_page_view_rate"
    return "primary_business_metric"


def _target_direction(metric: str) -> str:
    return "decrease" if metric.lower() in {"cpa", "cpc", "cpm", "cost_per_result"} else "increase"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text_or_default(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _dedupe_text(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
