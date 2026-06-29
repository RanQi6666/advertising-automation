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
    guardrail_text = " ".join(strategy["compliance_guardrails"])
    assert "real-money gambling" in guardrail_text
    assert "deposit/recharge" in guardrail_text
    assert "withdrawal" in guardrail_text
    assert "guaranteed winning" in guardrail_text


def test_ecommerce_strategy_does_not_receive_game_specific_guardrails() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "Glow Serum",
            "landing_url": "https://shop.example.sg/products/glow-serum",
            "country": "Singapore",
            "brief": "Promote skincare for office workers.",
        },
        today=date(2026, 6, 28),
    )

    guardrail_text = " ".join(strategy["compliance_guardrails"])
    assert strategy["vertical"] == "ecommerce"
    assert "real-money gambling" not in guardrail_text
    assert "deposit/recharge" not in guardrail_text


def test_weak_vertical_signals_fall_back_to_ecommerce_not_unknown() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "Daily Planner",
            "landing_url": "https://example.com/info",
            "country": "Malaysia",
            "brief": "Promote a practical daily-use app.",
        },
        today=date(2026, 6, 28),
    )

    assert strategy["vertical"] == "ecommerce"
    assert strategy["market_context"]["country_code"] == "MY"
    assert strategy["topic_angle_plan"][0]["angle_type"] == "pain_point"
    assert strategy["classification"]["fallback"] is True
    assert strategy["classification"]["confidence"] < 0.65


def test_gaja_game_landing_and_first_recharge_classify_as_game() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "country": "India",
            "event_name": "first_recharge",
            "work_order": {
                "parsed_fields": {
                    "media": "fb",
                    "event_name": "first_recharge",
                    "audience_description_raw": "年龄18-65",
                }
            },
        },
        today=date(2026, 6, 29),
    )

    assert strategy["vertical"] == "game"
    assert strategy["classification"]["fallback"] is False
    assert "game_tld" in strategy["classification"]["signals"]
    assert {item["angle_type"] for item in strategy["topic_angle_plan"]} == {
        "challenge_failure",
        "comeback_growth",
        "reward_burst",
    }


def test_localized_country_aliases_resolve_to_market_context() -> None:
    singapore_strategy = build_creative_strategy(
        {"product_name": "Glow Serum", "country": "新加坡"},
        today=date(2026, 6, 28),
    )
    malaysia_strategy = build_creative_strategy(
        {"product_name": "Daily Planner", "country": "马来西亚"},
        today=date(2026, 6, 28),
    )

    assert singapore_strategy["market_context"]["country_code"] == "SG"
    assert malaysia_strategy["market_context"]["country_code"] == "MY"


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
