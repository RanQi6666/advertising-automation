from __future__ import annotations

from math import isfinite
from typing import Any

COMPONENT_LIMITS = {
    "gameplay_ui": 35.0,
    "betting_mechanism": 25.0,
    "in_game_value_ui": 15.0,
    "gambling_style": 10.0,
    "visual_clarity": 10.0,
    "media_quality": 5.0,
}
PRIORITY_RANK = {
    "game_gambling": 3,
    "sports_betting": 2,
    "gambling_adjacent": 1,
    "unrelated": 0,
}
NEGATIVE_CAPS = {
    "recruitment_income": 8.0,
    "money_wallet_only": 10.0,
    "story_talking_head": 12.0,
    "ordinary_game": 24.0,
    "weak_gambling_game": 49.0,
}


def bounded(value: Any, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    parsed = float(value)
    if not isfinite(parsed):
        return 0.0
    return min(max(parsed, 0.0), maximum)


def validate_visual_score(data: Any, *, frame_count: int) -> dict[str, Any]:
    """Normalize only model-supplied visual evidence into a capped visual score."""
    payload = data if isinstance(data, dict) else {}
    priority = (
        payload.get("visual_priority")
        if payload.get("visual_priority") in PRIORITY_RANK
        else "unrelated"
    )
    negative = (
        payload.get("negative_visual_type")
        if payload.get("negative_visual_type") in {"none", *NEGATIVE_CAPS}
        else "none"
    )
    game = payload.get("game_context_present") is True
    betting = payload.get("betting_context_present") is True
    money_only = payload.get("money_only_promo") is True
    source = payload.get("component_scores")
    source = source if isinstance(source, dict) else {}
    components = {
        key: round(bounded(source.get(key), maximum), 2)
        for key, maximum in COMPONENT_LIMITS.items()
    }

    if money_only and not game:
        negative = "money_wallet_only"
    if priority == "gambling_adjacent" and not game:
        priority = "unrelated"
    if priority == "game_gambling" and not (game and betting):
        priority = "gambling_adjacent" if game else "unrelated"
    if priority == "sports_betting" and not betting:
        priority = "unrelated"

    try:
        safe_frame_count = max(int(frame_count), 0)
    except (TypeError, ValueError, OverflowError):
        safe_frame_count = 0
    total = min(sum(components.values()), NEGATIVE_CAPS.get(negative, 100.0))
    return {
        "visual_priority": priority,
        "game_context_present": game,
        "betting_context_present": betting,
        "money_only_promo": money_only,
        "negative_visual_type": negative,
        "component_scores": components,
        "analysis_confidence": round(bounded(payload.get("analysis_confidence"), 1.0), 4),
        "visual_evidence": (
            payload.get("visual_evidence", [])[:8]
            if isinstance(payload.get("visual_evidence"), list)
            else []
        ),
        "retrieval_hints": (
            payload.get("retrieval_hints", [])[:8]
            if isinstance(payload.get("retrieval_hints"), list)
            else []
        ),
        "frame_count": safe_frame_count,
        "visual_total": round(total, 2),
    }


def is_quality_candidate(score: Any) -> bool:
    if not isinstance(score, dict):
        return False
    priority = score.get("visual_priority")
    return priority in {"game_gambling", "sports_betting"} or (
        priority == "gambling_adjacent" and score.get("game_context_present") is True
    )


def _nonnegative_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    parsed = float(value)
    return max(parsed, 0.0) if isfinite(parsed) else 0.0


def _score_value(value: Any) -> float:
    return min(_nonnegative_number(value), 100.0)


def _component_sort_values(ad: dict[str, Any]) -> tuple[float, ...]:
    source = ad.get("component_scores")
    source = source if isinstance(source, dict) else {}
    return tuple(-bounded(source.get(name), maximum) for name, maximum in COMPONENT_LIMITS.items())


def _has_user_query_origin(ad: dict[str, Any]) -> bool:
    origins = ad.get("matched_query_origins")
    if not isinstance(origins, list):
        origin = ad.get("query_origin")
        origins = [origin] if isinstance(origin, str) else []
    return any(origin in {"user_exact", "user_expanded"} for origin in origins)


def visual_sort_key(ad: dict[str, Any]) -> tuple[Any, ...]:
    """Sort deterministic visual quality first; query origin only resolves exact visual ties."""
    priority = ad.get("visual_priority")
    priority_rank = PRIORITY_RANK.get(priority, PRIORITY_RANK["unrelated"])
    return (
        0 if is_quality_candidate(ad) else 1,
        -priority_rank,
        -_score_value(ad.get("visual_total")),
        *_component_sort_values(ad),
        -bounded(ad.get("analysis_confidence"), 1.0),
        -_nonnegative_number(ad.get("active_days")),
        0 if _has_user_query_origin(ad) else 1,
        str(ad.get("ad_library_id") or ""),
    )


def select_ranked(scored: list[dict[str, Any]], target_count: int) -> list[dict[str, Any]]:
    """Return distinct ranked ads and transparently label P4 non-quality fallback rows."""
    try:
        limit = max(int(target_count), 0)
    except (TypeError, ValueError, OverflowError):
        limit = 0
    selected: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    candidates = (item for item in scored if isinstance(item, dict))
    for candidate in sorted(candidates, key=visual_sort_key):
        ad_library_id = str(candidate.get("ad_library_id") or "")
        dedupe_key = ad_library_id or f"__anonymous__:{id(candidate)}"
        if dedupe_key in seen_ids:
            continue
        seen_ids.add(dedupe_key)
        fallback = not is_quality_candidate(candidate)
        selected.append(
            {
                **candidate,
                "is_fallback": fallback,
                "fallback_reason": "insufficient_high_relevance_candidates" if fallback else None,
            }
        )
        if len(selected) >= limit:
            break
    return selected


def quality_summary(
    selected: list[dict[str, Any]], *, target_count: int
) -> dict[str, int | bool | str]:
    """Summarize whether selected rows meet the visual-quality target or need fallback."""
    try:
        target = max(int(target_count), 0)
    except (TypeError, ValueError, OverflowError):
        target = 0
    qualified_count = sum(is_quality_candidate(candidate) for candidate in selected)
    fallback_count = sum(bool(candidate.get("is_fallback")) for candidate in selected)
    quality_target_met = qualified_count >= target
    if len(selected) < target:
        quality_grade = "insufficient_source_inventory"
    elif fallback_count:
        quality_grade = "fallback_used"
    else:
        quality_grade = "complete"
    return {
        "qualified_visual_count": qualified_count,
        "quality_target_met": quality_target_met,
        "fallback_count": fallback_count,
        "quality_grade": quality_grade,
    }