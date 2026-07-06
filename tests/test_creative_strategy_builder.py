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


def test_builds_operator_selected_gambling_strategy_with_vfx_library_policy() -> None:
    strategy = build_creative_strategy(
        {
            "work_order_type": "gambling",
            "product_name": "GAJA777",
            "project_name": "GAJA777 India",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "country": "India",
            "event_name": "first_recharge",
            "brief": "Create a premium gambling-like short video without money claims.",
        },
        today=date(2026, 7, 3),
    )

    assert strategy["schema_version"] == "creative_strategy.v2"
    assert strategy["vertical"] == "gambling"
    assert strategy["creative_package"] == "gambling_vfx_spectacle_package"
    assert strategy["visual_language"] == "boss_or_mysterious_energy_source_as_vfx_driver"
    assert "mystery_reveal_climax" in strategy["core_formula"]
    assert "portal_gate_or_vault_opens" not in strategy["core_formula"]
    assert strategy["classification"]["method"] == "operator_selected"
    assert strategy["brand_display"]["source_name"] == "GAJA777"
    assert strategy["brand_display"]["cleaned_brand"] == "GAJA"
    assert strategy["brand_display"]["digit_policy"] == "remove_digits_for_visible_brand"
    assert strategy["text_brand_timing_policy"] == {
        "text_allowed_windows": ["0-3s", "9-12s"],
        "middle_window": "3-9s",
        "middle_text_rule": "no text except optional tiny brand mark",
        "first_frame_role": "hook_and_brand",
        "last_frame_role": "brand_cta_close",
    }
    assert strategy["middle_vfx_policy"]["window"] == "3-9s"
    assert strategy["middle_vfx_policy"]["required_vfx_count"] == "2-3"
    assert strategy["middle_vfx_policy"]["source"] == "vfx_library"
    assert "not generic cinematic wording" in strategy["middle_vfx_policy"]["rule"]

    assert {item["angle_type"] for item in strategy["topic_angle_plan"]} == {
        "sky_rupture_spectacle",
        "dark_element_overload",
        "ancient_power_awakening",
    }
    assert {item["angle_type"] for item in strategy["topic_angle_plan"]}.isdisjoint(
        {"challenge_failure", "comeback_growth", "reward_burst"}
    )
    assert "bird_god" in strategy["boss_matrix"]["sky_rupture_spectacle"]
    assert "six_armed_overlord" in strategy["boss_matrix"]["dark_element_overload"]
    assert "serpent_guardian" in strategy["boss_matrix"]["ancient_power_awakening"]
    assert {
        "sky_rupture",
        "storm_eye",
        "energy_throne",
        "crystal_core",
        "golden_light_column",
        "ancient_seal_awakening",
        "abstract_power_vortex",
    } <= set(strategy["reveal_mechanism_pool"])
    assert "forbidden_gate" not in strategy["scene_pool"]
    assert "sealed_vault" not in strategy["scene_pool"]
    assert "at most one of the three gambling variants" in strategy["reveal_diversity_rule"]
    assert {
        "golden_particle_explosion",
        "divine_light_descent",
        "portal_gate_opening",
        "space_rupture",
        "element_burst",
        "slow_motion_suspension",
    } <= set(strategy["vfx_library"])
    assert "ENTER NOW" in strategy["cta_pool"]
    assert "MAKE YOUR CHOICE" in strategy["cta_pool"]

    guardrail_text = " ".join(strategy["compliance_guardrails"])
    assert "Do not show cash amounts" in guardrail_text
    assert "withdrawal/recharge/balance UI" in guardrail_text
    assert "slot machines" in guardrail_text
    assert "Guaranteed Win" in guardrail_text


def test_strategy_resolver_adds_dynamic_brand_profile_and_brand_policy() -> None:
    strategy = build_creative_strategy(
        {
            "work_order_type": "gambling",
            "project_name": "Royal Spin 88",
            "product_name": "Royal Spin 88",
            "country": "India",
            "brief": "Premium safe gambling-like spectacle.",
        },
        today=date(2026, 7, 6),
    )

    assert strategy["brand_profile"] == {
        "raw_name": "Royal Spin 88",
        "visible_name": "Royal Spin",
        "source_field": "product_name",
        "digit_policy": "remove_digits_for_visible_brand",
    }
    assert strategy["brand_policy_pack"]["source"] == "universal_brand_policy"
    assert "0-3s" in strategy["brand_policy_pack"]["visible_text_windows"]
    assert "9-12s" in strategy["brand_policy_pack"]["visible_text_windows"]
    assert "Do not invent brand names." in strategy["brand_policy_pack"]["forbidden"]


def test_india_gambling_uses_base_style_pack_with_country_overlay() -> None:
    strategy = build_creative_strategy(
        {
            "work_order_type": "gambling",
            "product_name": "GAJA777",
            "country": "India",
            "brief": "Create a premium gambling-like short video without money claims.",
        },
        today=date(2026, 7, 6),
    )

    assert strategy["style_pack_id"] == "gambling/IN/vfx_spectacle_current"
    assert strategy["style_pack"]["base_pack_id"] == "gambling/base/vfx_spectacle"
    assert strategy["style_pack"]["country_overlay_id"] == "country/overlays/IN"
    assert strategy["country_overlay"]["country_code"] == "IN"
    assert (
        "golden_light_column"
        in strategy["country_overlay"]["preferred_reveal_mechanisms"]
    )
    assert "real deity names or real religious figures" in strategy["country_overlay"]["avoid"]
    assert "bird_god" in strategy["boss_matrix"]["sky_rupture_spectacle"]
    assert "sandstone_festival_city" in strategy["scene_pool"]
    assert (
        "Boss or mysterious energy source is a VFX driver, not a combat character."
        in strategy["gambling_safety_rules"]
    )


def test_game_strategy_uses_india_boss_challenge_style_pack() -> None:
    strategy = build_creative_strategy(
        {
            "work_order_type": "game",
            "product_name": "Puzzle Quest 2",
            "country": "India",
            "brief": "Create a cinematic game ad with a playable boss challenge.",
        },
        today=date(2026, 7, 6),
    )

    assert strategy["style_pack_id"] == "game/IN/boss_challenge_fantasy"
    assert strategy["boss_guidance"]["role"] == "playable challenge obstacle"
    assert "player action" in strategy["boss_guidance"]["must_show"]
    assert "real-money gambling mechanics" in strategy["boss_guidance"]["must_avoid"]
    assert strategy["market_game_style_pack"]["style_pack_id"] == (
        "game/IN/boss_challenge_fantasy"
    )


def test_reference_signal_pack_is_preserved_without_overriding_style_pack() -> None:
    reference_signal_pack = {
        "source": "manual_reference_video_analysis",
        "rhythm_bias": ["0-3s brand plus boss arrival", "3-9s low-text VFX"],
        "visual_bias": ["golden_light_column", "storm_eye"],
    }
    strategy = build_creative_strategy(
        {
            "work_order_type": "gambling",
            "product_name": "Royal Spin 88",
            "country": "India",
            "reference_signal_pack": reference_signal_pack,
        },
        today=date(2026, 7, 6),
    )

    assert strategy["style_pack_id"] == "gambling/IN/vfx_spectacle_current"
    assert strategy["reference_signal_pack"] == reference_signal_pack
    assert "golden_light_column" in strategy["reveal_mechanism_pool"]


def test_builds_india_game_market_style_pack_for_male_18_24() -> None:
    strategy = build_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "country": "India",
            "work_order": {
                "parsed_fields": {
                    "gender": "Male",
                    "age_min": 18,
                    "age_max": 24,
                    "audience_description_raw": "India male 18-24 game interest",
                }
            },
            "brief": "Create a cinematic game ad with a playable challenge process.",
        },
        today=date(2026, 6, 29),
    )

    pack = strategy["market_game_style_pack"]

    assert strategy["vertical"] == "game"
    assert pack["source"] == "system_inferred"
    assert pack["country_code"] == "IN"
    assert pack["country_label"] == "India"
    assert pack["gender"] == "Male"
    assert pack["age_range"] == "18-24"
    assert "fast challenge and retry loop" in pack["game_interest_hypothesis"]
    assert "open-world action adventure" in pack["aaa_game_inspiration"]["genre_archetypes"]
    assert "cinematic RPG progression" in pack["aaa_game_inspiration"]["genre_archetypes"]
    assert "culture-inspired epic fantasy" in pack["visual_world"]
    assert pack["gameplay_process"]["player_goal"]
    assert pack["gameplay_process"]["opening_conflict"]
    assert pack["gameplay_process"]["player_actions"]
    assert pack["gameplay_process"]["progression_feedback"]
    assert pack["gameplay_process"]["ending_transition"]
    pack_text = str(pack).casefold()
    assert "game lobby" not in pack_text
    assert "app-lobby" not in pack_text
    assert "real deity names or real religious figures" in pack["cultural_safety"]["avoid"]
    assert (
        "prayers, worship, sacrifices, or ritual reenactments"
        in pack["cultural_safety"]["avoid"]
    )
    assert (
        "scripture, mantras, sacred text, or religious claims"
        in pack["cultural_safety"]["avoid"]
    )

    positive_text = " ".join(
        [
            str(pack["game_interest_hypothesis"]),
            str(pack["preferred_game_archetypes"]),
            str(pack["aaa_game_inspiration"]),
            str(pack["visual_world"]),
            str(pack["gameplay_process"]),
        ]
    ).casefold()
    for banned in (
        "ganesha",
        "shiva",
        "krishna",
        "prayer",
        "worship",
        "sacrifice",
        "ritual",
        "scripture",
        "casino",
        "slot",
        "jackpot",
        "cash",
        "coin",
        "recharge",
        "deposit",
        "withdraw",
    ):
        assert banned not in positive_text


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
    assert "market_game_style_pack" in compact
    assert compact["brand_profile"]["visible_name"] == "Puzzle Quest"
    assert compact["style_pack_id"] == "game/default/cinematic_mission"
    assert "raw_content" not in compact
    assert "SHOULD NOT LEAK" not in str(compact)


def test_compact_strategy_keeps_reference_signal_pack() -> None:
    strategy = build_creative_strategy(
        {
            "work_order_type": "gambling",
            "product_name": "Royal Spin 88",
            "country": "India",
            "reference_signal_pack": {
                "source": "manual_reference_video_analysis",
                "visual_bias": ["golden_light_column"],
            },
        },
        today=date(2026, 7, 6),
    )
    compact = compact_creative_strategy(strategy)

    assert compact is not None
    assert compact["style_pack_id"] == "gambling/IN/vfx_spectacle_current"
    assert compact["reference_signal_pack"]["visual_bias"] == ["golden_light_column"]


def test_compact_strategy_drops_disabled_gaja_brand_template() -> None:
    compact = compact_creative_strategy(
        {
            "template_id": "gaja_brand",
            "first_frame": {"visual_must_include": ["dark neon app lobby"]},
            "video_recipe": {"beats": ["0-2s: dark neon GAJA lobby hook"]},
        }
    )

    assert compact is None
