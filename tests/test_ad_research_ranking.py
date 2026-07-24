from __future__ import annotations

from backend.app.services.ad_research_ranking import (
    is_quality_candidate,
    quality_summary,
    select_ranked,
    validate_visual_score,
    visual_sort_key,
)


def test_cash_without_game_is_capped_at_ten() -> None:
    result = validate_visual_score(
        {
            "visual_priority": "gambling_adjacent",
            "game_context_present": False,
            "betting_context_present": False,
            "money_only_promo": True,
            "negative_visual_type": "money_wallet_only",
            "component_scores": {
                "in_game_value_ui": 15,
                "gambling_style": 10,
                "visual_clarity": 10,
            },
        },
        frame_count=4,
    )

    assert result["visual_priority"] == "unrelated"
    assert result["visual_total"] == 10
    assert not is_quality_candidate(result)


def test_recruitment_is_capped_at_eight() -> None:
    result = validate_visual_score(
        {
            "negative_visual_type": "recruitment_income",
            "component_scores": {"visual_clarity": 10, "media_quality": 5},
        },
        frame_count=4,
    )

    assert result["visual_total"] == 8


def test_slot_game_can_score_above_fifty() -> None:
    result = validate_visual_score(
        {
            "visual_priority": "game_gambling",
            "game_context_present": True,
            "betting_context_present": True,
            "negative_visual_type": "none",
            "component_scores": {
                "gameplay_ui": 34,
                "betting_mechanism": 23,
                "in_game_value_ui": 13,
                "gambling_style": 9,
                "visual_clarity": 9,
                "media_quality": 5,
            },
        },
        frame_count=4,
    )

    assert result["visual_total"] == 93
    assert is_quality_candidate(result)


def test_negative_caps_and_component_contract_are_deterministic() -> None:
    ordinary_game = validate_visual_score(
        {
            "visual_priority": "gambling_adjacent",
            "game_context_present": True,
            "negative_visual_type": "ordinary_game",
            "component_scores": {
                "gameplay_ui": 99,
                "betting_mechanism": -1,
                "in_game_value_ui": True,
                "gambling_style": "9",
                "visual_clarity": 10,
                "media_quality": 5,
            },
            "headline": "text must never participate",
            "category_match": True,
            "is_obviously_unrelated": False,
        },
        frame_count=3,
    )
    weak_gambling_game = validate_visual_score(
        {
            "visual_priority": "game_gambling",
            "game_context_present": True,
            "betting_context_present": True,
            "negative_visual_type": "weak_gambling_game",
            "component_scores": {
                "gameplay_ui": 35,
                "betting_mechanism": 25,
                "in_game_value_ui": 15,
                "gambling_style": 10,
                "visual_clarity": 10,
                "media_quality": 5,
            },
        },
        frame_count=3,
    )

    assert ordinary_game["component_scores"] == {
        "gameplay_ui": 35,
        "betting_mechanism": 0,
        "in_game_value_ui": 0,
        "gambling_style": 0,
        "visual_clarity": 10,
        "media_quality": 5,
    }
    assert ordinary_game["visual_total"] == 24
    assert "category_match" not in ordinary_game
    assert "is_obviously_unrelated" not in ordinary_game
    assert weak_gambling_game["visual_total"] == 49


def test_keyword_origin_only_breaks_complete_visual_tie() -> None:
    base = {
        "visual_priority": "game_gambling",
        "game_context_present": True,
        "visual_total": 80,
        "component_scores": {"gameplay_ui": 30, "betting_mechanism": 20},
        "analysis_confidence": 0.9,
        "active_days": 4,
    }
    model_ad = {
        **base,
        "ad_library_id": "model",
        "matched_query_origins": ["model_exploration"],
    }
    user_ad = {
        **base,
        "ad_library_id": "user",
        "matched_query_origins": ["user_exact"],
    }

    assert sorted([model_ad, user_ad], key=visual_sort_key)[0]["ad_library_id"] == "user"
    model_ad["visual_total"] = 81
    assert sorted([model_ad, user_ad], key=visual_sort_key)[0]["ad_library_id"] == "model"


def test_visual_sort_prioritizes_quality_and_visual_dimensions_before_query_origin() -> None:
    visual_leader = {
        "ad_library_id": "visual-leader",
        "visual_priority": "game_gambling",
        "game_context_present": True,
        "betting_context_present": True,
        "visual_total": 70,
        "component_scores": {"gameplay_ui": 35},
        "analysis_confidence": 0.4,
        "active_days": 1,
        "matched_query_origins": ["model_exploration"],
    }
    user_keyword = {
        **visual_leader,
        "ad_library_id": "user-keyword",
        "component_scores": {"gameplay_ui": 30},
        "analysis_confidence": 1.0,
        "active_days": 99,
        "matched_query_origins": ["user_expanded"],
    }
    adjacent = {
        **visual_leader,
        "ad_library_id": "adjacent",
        "visual_priority": "gambling_adjacent",
        "betting_context_present": False,
        "visual_total": 100,
    }

    assert [ad["ad_library_id"] for ad in sorted(
        [adjacent, user_keyword, visual_leader], key=visual_sort_key
    )] == ["visual-leader", "user-keyword", "adjacent"]


def test_visual_sort_keeps_active_days_descending_above_one_hundred() -> None:
    base = {
        "visual_priority": "game_gambling",
        "game_context_present": True,
        "betting_context_present": True,
        "visual_total": 80,
        "component_scores": {"gameplay_ui": 30, "betting_mechanism": 20},
        "analysis_confidence": 0.9,
    }
    older = {**base, "ad_library_id": "z-older", "active_days": 365}
    newer = {**base, "ad_library_id": "a-newer", "active_days": 100}

    assert sorted([newer, older], key=visual_sort_key)[0]["ad_library_id"] == "z-older"


def test_select_ranked_deduplicates_and_marks_only_non_quality_as_fallback() -> None:
    quality = {
        "ad_library_id": "quality",
        "visual_priority": "game_gambling",
        "game_context_present": True,
        "betting_context_present": True,
        "visual_total": 90,
        "component_scores": {"gameplay_ui": 35},
    }
    duplicate_quality = {**quality, "visual_total": 1}
    fallback = {
        "ad_library_id": "fallback",
        "visual_priority": "gambling_adjacent",
        "game_context_present": False,
        "visual_total": 95,
        "component_scores": {"visual_clarity": 10},
    }

    selected = select_ranked([fallback, duplicate_quality, quality], target_count=2)

    assert [item["ad_library_id"] for item in selected] == ["quality", "fallback"]
    assert [item["is_fallback"] for item in selected] == [False, True]
    assert selected[1]["fallback_reason"] == "insufficient_high_relevance_candidates"


def test_quality_summary_exposes_quality_target_fallback_and_grade() -> None:
    selected = [
        {
            "ad_library_id": "quality",
            "visual_priority": "sports_betting",
            "betting_context_present": True,
            "is_fallback": False,
        },
        {
            "ad_library_id": "fallback",
            "visual_priority": "unrelated",
            "is_fallback": True,
        },
    ]

    assert quality_summary(selected, target_count=2) == {
        "qualified_visual_count": 1,
        "quality_target_met": False,
        "fallback_count": 1,
        "quality_grade": "fallback_used",
    }
    assert quality_summary(selected[:1], target_count=2)["quality_grade"] == (
        "insufficient_source_inventory"
    )