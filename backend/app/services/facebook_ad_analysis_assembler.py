from __future__ import annotations

from typing import Any

from backend.app.schemas.facebook_ad_analysis import FacebookAdAnalysisResult

RULE_RESULT_SCHEMA_VERSION = "facebook_ad_analysis_v1"

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2}
_TARGETING_DIMENSIONS = {"country", "audience", "age", "gender", "device", "placement"}
_TARGETING_DECISIONS = {"adjust", "test", "monitor"}
_PLAN_CATEGORIES = {
    "landing_page",
    "tracking",
    "copywriting",
    "media",
    "targeting",
    "budget",
    "campaign_setup",
}
_DIMENSION_PERFORMANCE_CLAIMS = (
    "实际表现",
    "表现最好",
    "表现更好",
    "成效最好",
    "成效更好",
    "效果已经",
    "已得到证明",
    "转化更高",
    "成本更低",
    "best performing",
    "performs best",
    "better performance",
    "proven performance",
)


def assemble_facebook_ad_analysis(
    *,
    request_payload: dict[str, Any],
    rule_analysis: dict[str, Any],
    media_analysis: dict[str, Any] | None,
    public_research: dict[str, Any] | None,
    llm_contribution: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the public operator result while keeping rule-derived facts authoritative."""

    request_payload = _dict(request_payload)
    rule_analysis = _dict(rule_analysis)
    media_analysis = _dict(media_analysis)
    public_research = _dict(public_research)
    llm_contribution = _dict(llm_contribution)

    decision = _overall_decision(rule_analysis)
    plans = _adjustment_plans(rule_analysis, llm_contribution, decision)
    result = {
        "schema_version": RULE_RESULT_SCHEMA_VERSION,
        "platform": "facebook",
        "summary": _summary(decision, plans),
        "overall_decision": decision,
        "targeting_analysis": _targeting_analysis(request_payload, llm_contribution),
        "adjustment_plans": plans,
        "copywriting_analysis": _copywriting_analysis(request_payload, llm_contribution),
        "media_analysis": _media_analysis(
            request_payload,
            media_analysis,
            llm_contribution,
        ),
        "market_intelligence": _market_intelligence(
            public_research,
            llm_contribution,
        ),
        "data_gaps": _data_gaps(
            rule_analysis,
            media_analysis,
            public_research,
            llm_contribution,
        ),
    }
    return FacebookAdAnalysisResult.model_validate(result).model_dump(mode="json")


def _summary(decision: dict[str, str], plans: list[dict[str, str]]) -> str:
    first_action = plans[0]["title"] if plans else "先核对关键数据"
    text = f"{decision['main_problem']}，先{first_action}。"
    return _truncate(text, 100)


def _overall_decision(rule_analysis: dict[str, Any]) -> dict[str, str]:
    executive = _dict(rule_analysis.get("executive_summary"))
    action = str(executive.get("verdict") or "monitor").strip().lower()
    if action not in {"scale", "optimize", "monitor", "pause"}:
        action = "monitor"
    priority = _priority(executive.get("priority"))
    bottleneck = str(executive.get("primary_bottleneck") or "none").strip().lower()
    return {
        "action": action,
        "priority": priority,
        "main_problem": _main_problem(rule_analysis, bottleneck),
    }


def _main_problem(rule_analysis: dict[str, Any], bottleneck: str) -> str:
    if bottleneck == "landing_page" and _has_landing_page_evidence(rule_analysis):
        return "点击进入落地页后的流失较高"
    if _needs_tracking_verification(rule_analysis):
        return "关键转化事件数据不足，暂时无法判断主要流失环节"
    mapping = {
        "creative": "广告素材或文案需要进一步优化",
        "creative_hook": "广告开场或核心卖点的吸引力不足",
        "targeting": "当前定向设置值得通过小预算测试验证",
        "budget": "当前预算或投放节奏需要调整",
        "campaign_setup": "广告系列或广告组设置需要检查",
        "conversion": "点击后的业务转化表现需要优化",
        "none": "当前没有发现必须立即调整的明确问题",
    }
    return mapping.get(bottleneck, "当前投放需要继续观察并优先核对关键业务结果")


def _targeting_analysis(
    request_payload: dict[str, Any],
    llm_contribution: dict[str, Any],
) -> list[dict[str, str]]:
    values = _list(llm_contribution.get("targeting_analysis"))
    if not values:
        values = _list(_dict(llm_contribution.get("audience_and_delivery_analysis")).get("issues"))

    adset = _dict(request_payload.get("adset"))
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in values:
        if not isinstance(raw, dict):
            continue
        dimension = str(raw.get("dimension") or "").strip().lower()
        if dimension not in _TARGETING_DIMENSIONS:
            continue
        problem = _text(raw.get("problem"))
        suggestion = _text(raw.get("suggestion"))
        reason = _text(raw.get("reason"))
        if not problem or not suggestion or not reason:
            continue
        combined = f"{problem} {suggestion} {reason}".casefold()
        if any(claim.casefold() in combined for claim in _DIMENSION_PERFORMANCE_CLAIMS):
            continue
        if _is_targeting_placeholder(combined):
            continue
        decision = str(raw.get("decision") or "test").strip().lower()
        if decision not in _TARGETING_DECISIONS:
            decision = "test"
        # The public contract does not receive dimension-level performance breakdowns.
        # Therefore destructive direct changes are always converted into a test.
        if decision == "adjust":
            decision = "test"
        current = _text(raw.get("current")) or _current_targeting_value(adset, dimension)
        if not current:
            continue
        key = (dimension, problem.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(
            {
                "dimension": dimension,
                "current": _truncate(current, 600),
                "decision": decision,
                "problem": _truncate(problem, 600),
                "suggestion": _truncate(suggestion, 600),
                "reason": _truncate(reason, 600),
            }
        )
        if len(result) >= 3:
            break
    return result


def _adjustment_plans(
    rule_analysis: dict[str, Any],
    llm_contribution: dict[str, Any],
    decision: dict[str, str],
) -> list[dict[str, str]]:
    primary = _primary_plan(rule_analysis, decision)
    additional: list[dict[str, str]] = []
    values = _list(llm_contribution.get("adjustment_plans"))
    if not values:
        values = _list(llm_contribution.get("recommended_actions"))

    seen = {(primary["category"], primary["title"].casefold())}
    for index, raw in enumerate(values, start=1):
        plan = _normalize_plan(raw, index=index, decision=decision)
        if plan is None:
            continue
        key = (plan["category"], plan["title"].casefold())
        if key in seen or plan["category"] == primary["category"]:
            continue
        seen.add(key)
        additional.append(plan)

    additional.sort(key=lambda item: _PRIORITY_ORDER[item["priority"]])
    return [primary, *additional[:4]]


def _primary_plan(
    rule_analysis: dict[str, Any],
    decision: dict[str, str],
) -> dict[str, str]:
    if _has_landing_page_evidence(rule_analysis):
        metrics = _dict(rule_analysis.get("metrics"))
        clicks = _metric_display(metrics.get("inline_link_clicks"))
        landing_views = _metric_display(metrics.get("landing_page_views"))
        if clicks and landing_views:
            reason = f"广告产生{clicks}次链接点击，但只有{landing_views}次落地页浏览。"
        else:
            rate = _metric_display(metrics.get("landing_page_view_rate"), decimals=2)
            reason = (
                f"成功进入落地页的比例仅为{rate}%，点击后的页面到达环节存在明显损失。"
                if rate
                else "已提交数据表明，点击后的落地页到达环节是当前主要瓶颈。"
            )
        return {
            "priority": decision["priority"],
            "category": "landing_page",
            "title": "检查点击到落地页的流失",
            "action": "检查移动端页面加载速度、广告链接跳转、重定向链路和 Meta Pixel 事件回传。",
            "reason": reason,
            "expected_effect": "减少无效点击花费，提高有效落地页访问量。",
            "what_to_watch": "观察成功进入落地页的人数是否提高，以及购买或注册成本是否下降。",
        }
    if _needs_tracking_verification(rule_analysis):
        return {
            "priority": decision["priority"],
            "category": "tracking",
            "title": "核对关键事件回传",
            "action": "检查 Meta Pixel/CAPI 是否正常回传落地页浏览、购买、注册或其他核心业务事件。",
            "reason": "当前缺少关键事件数据，无法可靠判断点击后的主要流失环节。",
            "expected_effect": "补齐可用于判断真实业务效果的数据，避免根据不完整漏斗误调广告。",
            "what_to_watch": "观察关键事件是否持续稳定回传，并确认事件数量与业务后台一致。",
        }
    category = _plan_category(
        _dict(rule_analysis.get("executive_summary")).get("primary_bottleneck")
    )
    return {
        "priority": decision["priority"],
        "category": category,
        "title": "继续观察核心业务结果" if decision["action"] == "monitor" else "处理当前主要问题",
        "action": "保持原广告设置，继续收集足够的业务转化数据后再做单变量、小预算调整。",
        "reason": decision["main_problem"],
        "expected_effect": "降低样本不足导致误判和频繁修改投放设置的风险。",
        "what_to_watch": "观察花费、核心业务事件数量和单次业务结果成本是否形成稳定趋势。",
    }


def _normalize_plan(
    raw: Any,
    *,
    index: int,
    decision: dict[str, str],
) -> dict[str, str] | None:
    if isinstance(raw, str):
        action = raw.strip()
        if not action:
            return None
        return {
            "priority": "medium",
            "category": "campaign_setup",
            "title": _truncate(action, 100),
            "action": _truncate(action, 600),
            "reason": "该动作来自对当前广告数据和素材信息的综合分析。",
            "expected_effect": "验证该调整是否能够改善当前投放结果。",
            "what_to_watch": "观察核心业务事件和单次结果成本是否改善。",
        }
    if not isinstance(raw, dict):
        return None
    action = _text(raw.get("action"))
    if not action:
        return None
    category = _plan_category(raw.get("category"))
    title = _text(raw.get("title")) or f"执行优化动作{index}"
    reason = _text(raw.get("reason")) or decision["main_problem"]
    expected_effect = _text(raw.get("expected_effect")) or "验证该调整是否改善当前投放结果。"
    what_to_watch = _text(raw.get("what_to_watch") or raw.get("success_metric"))
    if not what_to_watch:
        what_to_watch = "观察核心业务事件和单次结果成本是否改善。"
    return {
        "priority": _priority(raw.get("priority")),
        "category": category,
        "title": _truncate(title, 100),
        "action": _truncate(action, 600),
        "reason": _truncate(reason, 600),
        "expected_effect": _truncate(expected_effect, 600),
        "what_to_watch": _truncate(what_to_watch, 600),
    }


def _copywriting_analysis(
    request_payload: dict[str, Any],
    llm_contribution: dict[str, Any],
) -> dict[str, Any]:
    creative = _dict(request_payload.get("creative"))
    source_values = [
        creative.get("message"),
        creative.get("primary_text"),
        creative.get("body"),
        creative.get("headline"),
        creative.get("title"),
        creative.get("description"),
        creative.get("caption"),
    ]
    has_source_copy = any(_text(value) for value in source_values)
    section = _dict(llm_contribution.get("copywriting_analysis"))
    old_creative = _dict(llm_contribution.get("creative_analysis"))
    if not has_source_copy:
        return {
            "summary": "未收到可供分析的原始广告文案。",
            "problems": [],
            "suggestions": [],
            "recommended_primary_text": None,
            "recommended_headline": None,
            "recommended_description": None,
        }

    summary = _text(section.get("summary") or old_creative.get("summary"))
    if not summary:
        summary = "当前文案已纳入分析，暂未发现必须立即重写的明确问题。"
    problems = _text_list(section.get("problems") or old_creative.get("weaknesses"), 3, 600)
    suggestions = _text_list(
        section.get("suggestions") or llm_contribution.get("creative_feedback"),
        3,
        600,
    )
    return {
        "summary": _truncate(summary, 100),
        "problems": problems,
        "suggestions": suggestions,
        "recommended_primary_text": _optional_text(section.get("recommended_primary_text"), 1200),
        "recommended_headline": _optional_text(section.get("recommended_headline"), 1200),
        "recommended_description": _optional_text(section.get("recommended_description"), 1200),
    }


def _media_analysis(
    request_payload: dict[str, Any],
    processing: dict[str, Any],
    llm_contribution: dict[str, Any],
) -> dict[str, Any]:
    creative = _dict(request_payload.get("creative"))
    media_type = str(creative.get("creative_type") or "image").strip().lower()
    if media_type not in {"image", "video"}:
        media_type = "video" if creative.get("video_url") else "image"
    status = str(processing.get("status") or "unavailable").strip().lower()
    section = _dict(llm_contribution.get("media_analysis"))
    if not section:
        section = _dict(llm_contribution.get("visual_analysis"))

    if status != "available":
        return {
            "media_type": media_type,
            "summary": "素材未能完成可靠的画面处理，本次不对具体画面内容作判断。",
            "improvements": [],
        }

    summary = _text(section.get("summary"))
    if not summary:
        summary = "素材已完成处理，暂未发现必须修改的明确画面问题。"
    improvements = _media_improvements(section)
    return {
        "media_type": media_type,
        "summary": _truncate(summary, 100),
        "improvements": improvements,
    }


def _media_improvements(section: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for raw in _list(section.get("improvements")):
        if not isinstance(raw, dict):
            continue
        location = _text(raw.get("location"))
        problem = _text(raw.get("problem"))
        action = _text(raw.get("action"))
        if not location or not problem or not action:
            continue
        result.append(
            {
                "location": _truncate(location, 600),
                "problem": _truncate(problem, 600),
                "action": _truncate(action, 600),
            }
        )
        if len(result) >= 3:
            return result

    recommendations = _text_list(section.get("recommendations"), 3, 600)
    weaknesses = _text_list(section.get("weaknesses"), 3, 600)
    for index, action in enumerate(recommendations):
        result.append(
            {
                "location": "素材画面",
                "problem": weaknesses[index]
                if index < len(weaknesses)
                else "当前画面表达可进一步优化。",
                "action": action,
            }
        )
    return result[:3]


def _market_intelligence(
    public_research: dict[str, Any],
    llm_contribution: dict[str, Any],
) -> dict[str, Any]:
    raw_references = _list(public_research.get("selected_reference_ads"))
    references: list[dict[str, str]] = []
    for raw in raw_references:
        if not isinstance(raw, dict):
            continue
        source_url = _text(raw.get("source_url"))
        if not source_url:
            continue
        patterns = _dict(raw.get("creative_patterns"))
        observed_pattern = _text(raw.get("observed_pattern")) or _first_text(
            patterns.get("public_excerpt"),
            patterns.get("public_title"),
        )
        learnings = _list(raw.get("applicable_learnings"))
        applicable_idea = _text(raw.get("applicable_idea")) or _first_text(*learnings)
        references.append(
            {
                "advertiser_name": _truncate(
                    _text(raw.get("advertiser_name")) or "公开来源广告主",
                    600,
                ),
                "source_url": _truncate(source_url, 2000),
                "observed_pattern": _truncate(
                    observed_pattern or "公开页面展示了与当前广告相关的创意表达。",
                    600,
                ),
                "applicable_idea": _truncate(
                    applicable_idea or "仅参考其创意钩子和表达方式，不把公开可见性当作成效证明。",
                    600,
                ),
            }
        )
        if len(references) >= 3:
            break

    raw_status = str(public_research.get("status") or "unavailable").strip().lower()
    if references and raw_status in {"succeeded", "completed"}:
        status = "completed"
    elif references:
        status = "partial"
    else:
        status = "unavailable"
    llm_section = _dict(llm_contribution.get("market_intelligence"))
    summary = _text(llm_section.get("summary"))
    if not summary:
        summary = (
            "已从公开来源整理相似广告的创意表达，供本次优化参考。"
            if references
            else "本次没有找到足够可靠的相似广告参考。"
        )
    return {
        "status": status,
        "summary": _truncate(summary, 100),
        "references": references,
        "limitation": "公开来源只能用于参考广告内容和创意模式，无法验证真实花费、购买量或 ROAS。",
    }


def _data_gaps(
    rule_analysis: dict[str, Any],
    media_analysis: dict[str, Any],
    public_research: dict[str, Any],
    llm_contribution: dict[str, Any],
) -> list[str]:
    gaps: list[str] = []
    metrics = _dict(rule_analysis.get("metrics"))
    if not _metric_available(metrics.get("landing_page_views")):
        gaps.append("缺少落地页浏览数据，因此无法判断点击后是否顺利进入页面。")
    if not any(
        _metric_available(metrics.get(name))
        for name in ("purchase", "lead", "complete_registration", "first_recharge")
    ):
        gaps.append("缺少购买、注册或线索等业务结果数据，因此暂时无法判断真实转化效果。")
    if str(media_analysis.get("status") or "unavailable").lower() != "available":
        gaps.append("素材下载或处理未完成，因此本次无法可靠判断图片或视频画面问题。")
    if (
        not _list(public_research.get("selected_reference_ads"))
        and len(gaps) < 3
        and str(public_research.get("status") or "unavailable").lower()
        in {"unavailable", "disabled"}
    ):
        gaps.append("本次未获得可靠的公开相似广告参考，因此市场创意对比只能作为缺失项处理。")

    for value in _text_list(llm_contribution.get("data_gaps"), 3, 600):
        if len(gaps) >= 3:
            break
        if _looks_like_technical_gap(value):
            continue
        gaps.append(value)
    return _dedupe_text(gaps)[:3]


def _has_landing_page_evidence(rule_analysis: dict[str, Any]) -> bool:
    metrics = _dict(rule_analysis.get("metrics"))
    rate = _dict(metrics.get("landing_page_view_rate"))
    executive = _dict(rule_analysis.get("executive_summary"))
    return rate.get("value") is not None and executive.get("primary_bottleneck") == "landing_page"


def _needs_tracking_verification(rule_analysis: dict[str, Any]) -> bool:
    metrics = _dict(rule_analysis.get("metrics"))
    if not _metric_available(metrics.get("landing_page_views")):
        return True
    return not any(
        _metric_available(metrics.get(name))
        for name in ("purchase", "lead", "complete_registration", "first_recharge")
    ) and not _has_landing_page_evidence(rule_analysis)


def _metric_available(value: Any) -> bool:
    metric = _dict(value)
    return metric.get("value") is not None and metric.get("source") != "missing"


def _metric_display(value: Any, *, decimals: int = 0) -> str | None:
    raw = _dict(value).get("value")
    if raw is None:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if decimals:
        return f"{number:.{decimals}f}"
    return str(int(number)) if number.is_integer() else f"{number:g}"


def _current_targeting_value(adset: dict[str, Any], dimension: str) -> str | None:
    mapping: dict[str, Any] = {
        "country": adset.get("countries") or adset.get("country"),
        "audience": adset.get("audience") or adset.get("audience_description"),
        "age": _age_range(adset),
        "gender": adset.get("genders") or adset.get("gender"),
        "device": adset.get("device_platforms") or adset.get("devices"),
        "placement": adset.get("placements") or adset.get("publisher_platforms"),
    }
    value = mapping.get(dimension)
    if isinstance(value, list):
        return ",".join(str(item) for item in value if str(item).strip()) or None
    return _text(value)


def _age_range(adset: dict[str, Any]) -> str | None:
    minimum = adset.get("age_min")
    maximum = adset.get("age_max")
    if minimum is None and maximum is None:
        return None
    return f"{minimum or '不限'}至{maximum or '不限'}岁"


def _is_targeting_placeholder(value: str) -> bool:
    placeholder_pairs = (
        ("继续观察", "暂无明确问题"),
        ("需要继续观察", "暂无"),
        ("monitor", "no clear issue"),
    )
    return any(all(fragment.casefold() in value for fragment in pair) for pair in placeholder_pairs)


def _looks_like_technical_gap(value: str) -> bool:
    lowered = value.casefold()
    return any(
        fragment in lowered
        for fragment in (
            "local_artifacts",
            "/app/storage",
            "traceback",
            "exception",
            "missing_metrics",
            "field name",
        )
    )


def _plan_category(value: Any) -> str:
    text = str(value or "campaign_setup").strip().lower()
    aliases = {
        "measurement": "tracking",
        "creative": "media",
        "creative_hook": "media",
        "audience": "targeting",
        "delivery": "budget",
        "performance": "campaign_setup",
        "none": "campaign_setup",
    }
    text = aliases.get(text, text)
    return text if text in _PLAN_CATEGORIES else "campaign_setup"


def _priority(value: Any) -> str:
    if isinstance(value, int | float):
        number = int(value)
        return "high" if number <= 2 else "medium" if number <= 5 else "low"
    text = str(value or "medium").strip().lower()
    if text == "critical":
        return "high"
    return text if text in _PRIORITY_ORDER else "medium"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _optional_text(value: Any, limit: int) -> str | None:
    text = _text(value)
    return _truncate(text, limit) if text else None


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _text(value)
        if text:
            return text
    return None


def _text_list(value: Any, limit: int, text_limit: int) -> list[str]:
    result: list[str] = []
    for item in _list(value):
        text = _text(item)
        if not text:
            continue
        result.append(_truncate(text, text_limit))
        if len(result) >= limit:
            break
    return _dedupe_text(result)


def _dedupe_text(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit].rstrip()
