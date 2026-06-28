from datetime import date

from backend.app.services.creative_strategy_builder import (
    CREATIVE_STRATEGY_SCHEMA_VERSION,
    build_creative_strategy,
    compact_creative_strategy,
)


def test_builds_singapore_ecommerce_strategy_for_female_25_34() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "Glow Serum",
            "landing_url": "https://shop.example.sg/products/glow-serum",
            "country": "Singapore",
            "work_order": {
                "parsed_fields": {
                    "gender": "Female",
                    "age_min": 25,
                    "age_max": 34,
                    "audience_description_raw": "Female 25-34, office workers",
                }
            },
            "landing_page": {
                "title": "Glow Serum",
                "description": "Bright-looking skin for busy routines",
                "text_excerpt": "Shop now. 30% off. Daily skincare for humid weather.",
                "headings": ["Hydrating glow", "Fast routine", "Limited offer"],
            },
            "brief": "Promote skincare for office workers.",
        },
        today=date(2026, 6, 28),
    )

    assert strategy["schema_version"] == CREATIVE_STRATEGY_SCHEMA_VERSION
    assert strategy["vertical"] == "ecommerce"
    assert strategy["market_context"]["country_code"] == "SG"
    assert strategy["market_context"]["language"] == "English"
    assert strategy["market_context"]["buying_power"] == "high"
    assert strategy["audience_lens"]["gender"] == "Female"
    assert strategy["audience_lens"]["age_range"] == "25-34"
    assert "work pressure" in strategy["audience_lens"]["pain_points"]
    assert "quality of life" in strategy["audience_lens"]["buying_motivations"]
    assert [item["slot"] for item in strategy["topic_angle_plan"]] == [1, 2, 3]
    assert len({item["angle_type"] for item in strategy["topic_angle_plan"]}) == 3
    assert {item["angle_type"] for item in strategy["topic_angle_plan"]} <= {
        "pain_point",
        "scenario_resonance",
        "social_recommendation",
        "value_offer",
        "before_after_safe",
    }
    assert strategy["video_guidance"]["duration_adaptive"] is True
    assert "Do not claim guaranteed results." in strategy["compliance_guardrails"]


def test_builds_game_strategy_without_gaja_default_template() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "Puzzle Quest",
            "landing_url": "https://play.example.com/level-challenge",
            "country": "US",
            "landing_page": {
                "title": "Puzzle level challenge",
                "text_excerpt": "Can you beat level 10? Play the puzzle challenge.",
            },
            "brief": "Make a game ad with a level challenge.",
        },
        today=date(2026, 6, 28),
    )

    assert strategy["schema_version"] == "creative_strategy.v2"
    assert strategy["vertical"] == "game"
    assert "template_id" not in strategy
    assert strategy["classification"]["confidence"] >= 0.65
    assert {item["angle_type"] for item in strategy["topic_angle_plan"]} == {
        "challenge_failure",
        "comeback_growth",
        "reward_burst",
    }
    assert "0-3s" not in str(strategy["video_guidance"])
    assert "duration_seconds" not in str(strategy["video_guidance"])


def test_unknown_vertical_uses_conservative_generic_plan() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "Daily Planner",
            "landing_url": "https://example.com/info",
            "country": "Malaysia",
            "brief": "Promote a practical daily-use app.",
        },
        today=date(2026, 6, 28),
    )

    assert strategy["vertical"] == "unknown"
    assert strategy["market_context"]["country_code"] == "MY"
    assert strategy["topic_angle_plan"][0]["angle_type"] == "scenario_resonance"
    assert strategy["classification"]["confidence"] < 0.65


def test_nearby_holidays_are_deterministic_and_do_not_invent_trends() -> None:
    strategy = build_creative_strategy(
        {"product_name": "Shop", "country": "Singapore", "landing_url": "https://shop.sg"},
        today=date(2026, 7, 30),
    )

    holiday_names = [item["name"] for item in strategy["market_context"]["nearby_holidays"]]
    assert "National Day" in holiday_names
    assert strategy["market_context"]["local_trend_notes"] == []
    assert "Do not invent local trending topics." in strategy["compliance_guardrails"]


def test_compact_strategy_keeps_v2_fields_and_drops_large_unknown_blob() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "Puzzle Quest",
            "landing_url": "https://play.example.com/game",
            "country": "US",
            "brief": "game challenge",
        },
        today=date(2026, 6, 28),
    )
    strategy["raw_content"] = "SHOULD NOT LEAK"
    compact = compact_creative_strategy(strategy)

    assert compact is not None
    assert compact["schema_version"] == "creative_strategy.v2"
    assert compact["vertical"] == "game"
    assert "topic_angle_plan" in compact
    assert "raw_content" not in compact
    assert "SHOULD NOT LEAK" not in str(compact)
