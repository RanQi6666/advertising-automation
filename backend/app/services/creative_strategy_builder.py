from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any
from urllib.parse import urlparse

CREATIVE_STRATEGY_SCHEMA_VERSION = "creative_strategy.v2"

GAME_KEYWORDS = (
    "game",
    "play",
    "level",
    "challenge",
    "reward",
    "character",
    "battle",
    "puzzle",
    "quest",
    "runner",
    "mini game",
    "first_recharge",
)
ECOMMERCE_KEYWORDS = (
    "shop",
    "cart",
    "price",
    "discount",
    "shipping",
    "cod",
    "product",
    "review",
    "before after",
    "skincare",
    "fitness",
    "serum",
    "limited offer",
)
WORK_ORDER_TYPES = ("ecommerce", "game", "gambling")
TEXT_BRAND_TIMING_POLICY = {
    "text_allowed_windows": ["0-3s", "9-12s"],
    "middle_window": "3-9s",
    "middle_text_rule": "no text except optional tiny brand mark",
    "first_frame_role": "hook_and_brand",
    "last_frame_role": "brand_cta_close",
}
MIDDLE_VFX_POLICY = {
    "window": "3-9s",
    "required_vfx_count": "2-3",
    "source": "vfx_library",
    "rule": (
        "middle segment must be built from selected VFX library items, "
        "not generic cinematic wording"
    ),
}
VFX_LIBRARY = [
    "golden_particle_explosion",
    "divine_light_descent",
    "portal_gate_opening",
    "space_rupture",
    "element_burst",
    "slow_motion_suspension",
    "feather_meteor_storm",
    "scale_particle_vortex",
    "energy_serpent_vortex",
    "boss_energy_awakening",
    "seal_shatter",
    "vault_or_relic_mechanism_startup",
    "epic_legendary_big_reward_feedback",
    "score_points_stars_power_roll",
]
GAMBLING_BOSS_MATRIX = {
    "sky_rupture_spectacle": [
        "bird_god",
        "phoenix_king",
        "storm_bird_king",
        "dragon_descendant_lord",
        "four_wing_monarch",
        "golden_wing_sky_lord",
    ],
    "dark_element_overload": [
        "six_armed_overlord",
        "shadow_monarch",
        "abyss_titan",
        "thunder_king",
        "frost_judge",
        "mechanical_deity",
    ],
    "ancient_power_awakening": [
        "serpent_guardian",
        "golden_scale_serpent",
        "golden_statue",
        "temple_guard",
        "ancient_knight",
        "great_sword_judge",
    ],
}
GAMBLING_SCENE_POOL = [
    "sky_temple",
    "ancient_ruins",
    "storm_cloud_realm",
    "golden_sanctum",
    "shadow_energy_throne",
    "floating_crystal_citadel",
    "sandstone_festival_city",
]
GAMBLING_REVEAL_MECHANISM_POOL = [
    "sky_rupture",
    "storm_eye",
    "energy_throne",
    "crystal_core",
    "golden_light_column",
    "ancient_seal_awakening",
    "abstract_power_vortex",
    "forbidden_gate_optional",
    "portal_gate_optional",
    "sealed_vault_optional",
]
GAMBLING_CTA_POOL = [
    "ENTER NOW",
    "MAKE YOUR CHOICE",
    "UNLOCK NOW",
    "FACE THE TRIAL",
    "PLAY NOW",
]

COUNTRY_CONTEXT = {
    "SG": {
        "country": "Singapore",
        "language": "English",
        "buying_power": "high",
        "culture_notes": ["concise English", "quality and trust matter", "urban routine"],
        "religion_or_customs": ["multicultural market; avoid stereotypes"],
    },
    "US": {
        "country": "United States",
        "language": "English",
        "buying_power": "high",
        "culture_notes": ["direct benefit-led messaging", "clear proof and convenience"],
        "religion_or_customs": ["avoid political or identity assumptions"],
    },
    "IN": {
        "country": "India",
        "language": "Hindi or English",
        "buying_power": "value_sensitive",
        "culture_notes": ["value clarity", "mobile-first usage", "festival season sensitivity"],
        "religion_or_customs": ["avoid sacred symbols, rituals, or religious stereotypes"],
    },
    "ID": {
        "country": "Indonesia",
        "language": "Indonesian",
        "buying_power": "value_sensitive",
        "culture_notes": ["mobile-first", "promo-sensitive", "community proof"],
        "religion_or_customs": ["respect Ramadan and modest cultural cues"],
    },
    "MY": {
        "country": "Malaysia",
        "language": "Malay or English",
        "buying_power": "medium",
        "culture_notes": ["practical value", "trust", "multilingual context"],
        "religion_or_customs": ["respect Ramadan and halal/modesty sensitivities"],
    },
    "TH": {
        "country": "Thailand",
        "language": "Thai",
        "buying_power": "value_sensitive",
        "culture_notes": ["friendly tone", "visual clarity", "promo sensitivity"],
        "religion_or_customs": ["avoid religious imagery and royal references"],
    },
    "VN": {
        "country": "Vietnam",
        "language": "Vietnamese",
        "buying_power": "value_sensitive",
        "culture_notes": ["deal clarity", "fast mobile commerce", "practical benefits"],
        "religion_or_customs": ["avoid political and sensitive historical references"],
    },
    "BR": {
        "country": "Brazil",
        "language": "Brazilian Portuguese",
        "buying_power": "medium",
        "culture_notes": ["energetic tone", "social proof", "mobile-first"],
        "religion_or_customs": ["avoid stereotypes around region, race, or religion"],
    },
    "MX": {
        "country": "Mexico",
        "language": "Spanish for Mexico",
        "buying_power": "medium",
        "culture_notes": ["clear value", "family and daily-life scenes when relevant"],
        "religion_or_customs": ["avoid religious or cultural costume stereotypes"],
    },
}

COUNTRY_ALIASES = {
    "singapore": "SG",
    "sg": "SG",
    "新加坡": "SG",
    "united states": "US",
    "usa": "US",
    "us": "US",
    "美国": "US",
    "india": "IN",
    "in": "IN",
    "印度": "IN",
    "भारत": "IN",
    "indonesia": "ID",
    "id": "ID",
    "印度尼西亚": "ID",
    "印尼": "ID",
    "malaysia": "MY",
    "my": "MY",
    "马来西亚": "MY",
    "thailand": "TH",
    "th": "TH",
    "泰国": "TH",
    "ประเทศไทย": "TH",
    "vietnam": "VN",
    "vn": "VN",
    "越南": "VN",
    "việt nam": "VN",
    "brazil": "BR",
    "br": "BR",
    "巴西": "BR",
    "mexico": "MX",
    "mx": "MX",
    "墨西哥": "MX",
    "méxico": "MX",
}

HOLIDAYS = {
    "SG": [("National Day", (8, 9)), ("Singles' Day", (11, 11)), ("Christmas", (12, 25))],
    "US": [("Independence Day", (7, 4)), ("Black Friday", (11, 27)), ("Christmas", (12, 25))],
    "IN": [("Independence Day", (8, 15)), ("Diwali season", (11, 8))],
    "ID": [("Independence Day", (8, 17)), ("Singles' Day", (11, 11))],
    "MY": [("National Day", (8, 31)), ("Singles' Day", (11, 11))],
    "TH": [("Mother's Day", (8, 12)), ("Singles' Day", (11, 11))],
    "VN": [("National Day", (9, 2)), ("Singles' Day", (11, 11))],
    "BR": [("Independence Day", (9, 7)), ("Black Friday", (11, 27))],
    "MX": [("Independence Day", (9, 16)), ("Buen Fin season", (11, 15))],
}


def _build_creative_strategy_base(
    context: Mapping[str, Any],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    current_date = today or date.today()
    country_code = _country_code(context)
    market_context = _market_context(country_code, current_date)
    audience_lens = _audience_lens(context)
    vertical, classification = _classify_vertical(context)
    topic_angle_plan = _topic_angle_plan(vertical)
    brand_display = _brand_display(context)
    strategy = {
        "schema_version": CREATIVE_STRATEGY_SCHEMA_VERSION,
        "vertical": vertical,
        "classification": classification,
        "market_context": market_context,
        "audience_lens": audience_lens,
        "brand_display": brand_display,
        "text_brand_timing_policy": TEXT_BRAND_TIMING_POLICY,
        "middle_vfx_policy": MIDDLE_VFX_POLICY,
        "vfx_library": VFX_LIBRARY,
        "topic_angle_plan": topic_angle_plan,
        "copy_guidance": _copy_guidance(vertical, market_context, audience_lens),
        "image_guidance": _image_guidance(vertical),
        "video_guidance": _video_guidance(vertical),
        "compliance_guardrails": _compliance_guardrails(vertical),
    }
    if vertical == "gambling":
        strategy.update(_gambling_creative_package())
    market_game_style_pack = _market_game_style_pack(
        context=context,
        market_context=market_context,
        audience_lens=audience_lens,
        vertical=vertical,
    )
    if market_game_style_pack:
        strategy["market_game_style_pack"] = market_game_style_pack
    return strategy


def build_creative_strategy(
    context: Mapping[str, Any],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    from backend.app.services.creative_strategy.resolver import resolve_creative_strategy

    return resolve_creative_strategy(context, today=today)


def compact_creative_strategy(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    if value.get("template_id") == "gaja_brand":
        return None
    allowed = (
        "schema_version",
        "vertical",
        "classification",
        "market_context",
        "audience_lens",
        "brand_display",
        "brand_profile",
        "brand_policy_pack",
        "text_brand_timing_policy",
        "middle_vfx_policy",
        "vfx_library",
        "style_pack_id",
        "style_pack",
        "country_overlay",
        "boss_guidance",
        "creative_package",
        "visual_language",
        "core_formula",
        "boss_matrix",
        "scene_pool",
        "reveal_mechanism_pool",
        "reveal_diversity_rule",
        "cta_pool",
        "gambling_safety_rules",
        "topic_angle_plan",
        "copy_guidance",
        "image_guidance",
        "video_guidance",
        "market_game_style_pack",
        "reference_signal_pack",
        "compliance_guardrails",
        # legacy keys remain allowed so older metadata still works downstream
        "template_id",
        "template_name",
        "duration_seconds",
        "aspect_ratio",
        "brand",
        "first_frame",
        "last_frame",
        "motion_direction",
        "landing_visual_reference",
        "negative_style_cues",
        "video_recipe",
        "country_style_pack",
        "visual_concepts",
        "text_layout_rules",
        "first_three_seconds",
    )
    compact = {key: value[key] for key in allowed if value.get(key) not in (None, "", [])}
    return compact or None


def _collect_text(value: Any, parts: list[str]) -> None:
    if value is None:
        return
    if isinstance(value, str):
        parts.append(value)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            _collect_text(key, parts)
            _collect_text(item, parts)
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for item in value:
            _collect_text(item, parts)
        return
    parts.append(str(value))


def _country_code(context: Mapping[str, Any]) -> str:
    candidates = [
        context.get("country"),
        _nested_value(context.get("work_order"), "country"),
        _nested_value(context.get("work_order"), "country_code"),
        _nested_value(context.get("work_order"), "target_country"),
        _nested_value(context.get("structured_fields"), "country"),
        _nested_value(context.get("reviewed_fields"), "country"),
    ]
    for value in candidates:
        country_code = _normalize_country_code(value)
        if country_code:
            return country_code

    host = urlparse(_string_value(context.get("landing_url"))).hostname or ""
    tld = host.rsplit(".", 1)[-1].casefold()
    return COUNTRY_ALIASES.get(tld, "US")


def _market_context(country_code: str, current_date: date) -> dict[str, Any]:
    country_context = COUNTRY_CONTEXT.get(country_code, COUNTRY_CONTEXT["US"])
    return {
        "country_code": country_code,
        **country_context,
        "nearby_holidays": _nearby_holidays(country_code, current_date),
        "local_trend_notes": [],
    }


def _nearby_holidays(country_code: str, current_date: date) -> list[dict[str, Any]]:
    nearby: list[dict[str, Any]] = []
    for name, (month, day) in HOLIDAYS.get(country_code, []):
        holiday_date = date(current_date.year, month, day)
        if holiday_date < current_date:
            holiday_date = date(current_date.year + 1, month, day)
        days_until = (holiday_date - current_date).days
        if days_until <= 45:
            nearby.append(
                {
                    "name": name,
                    "date": holiday_date.isoformat(),
                    "days_until": days_until,
                }
            )
    return nearby


def _audience_lens(context: Mapping[str, Any]) -> dict[str, Any]:
    gender = _first_non_empty(
        _nested_value(context.get("work_order"), "gender"),
        _nested_value(context.get("structured_fields"), "gender"),
        _nested_value(context.get("reviewed_fields"), "gender"),
    )
    age_min = _first_non_empty(
        _nested_value(context.get("work_order"), "age_min"),
        _nested_value(context.get("structured_fields"), "age_min"),
        _nested_value(context.get("reviewed_fields"), "age_min"),
    )
    age_max = _first_non_empty(
        _nested_value(context.get("work_order"), "age_max"),
        _nested_value(context.get("structured_fields"), "age_max"),
        _nested_value(context.get("reviewed_fields"), "age_max"),
    )
    audience_description = _first_non_empty(
        context.get("audience_description"),
        _nested_value(context.get("work_order"), "audience_description_raw"),
        _nested_value(context.get("structured_fields"), "audience_description"),
        _nested_value(context.get("reviewed_fields"), "audience_description"),
    )
    return {
        "gender": _string_value(gender) or "All",
        "age_range": _age_range(age_min, age_max),
        "description": _string_value(audience_description),
        "pain_points": _pain_points(context),
        "buying_motivations": _buying_motivations(context),
    }


def _brand_display(context: Mapping[str, Any]) -> dict[str, Any]:
    source_name = _first_non_empty(
        context.get("product_name"),
        context.get("project_name"),
        context.get("campaign_name"),
        _nested_value(context.get("structured_fields"), "product_name"),
        _nested_value(context.get("structured_fields"), "project_name"),
        _nested_value(context.get("work_order"), "product_name"),
        _nested_value(context.get("work_order"), "project_name"),
        _nested_value(context.get("work_order"), "campaign_name"),
        _nested_value(context.get("landing_page"), "title"),
    )
    source_text = _string_value(source_name).strip()
    cleaned = re.sub(r"\d+", "", source_text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|")
    if not cleaned:
        cleaned = source_text
    return {
        "source_name": source_text,
        "cleaned_brand": cleaned,
        "digit_policy": "remove_digits_for_visible_brand",
        "vip_required_for_gambling": True,
    }


def _classify_vertical(context: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    selected_type = _selected_work_order_type(context)
    if selected_type:
        return selected_type, {
            "method": "operator_selected",
            "confidence": 1.0,
            "signals": [selected_type],
            "fallback": False,
            "source": "work_order_type",
        }

    text = _context_text(context)
    game_hits = _game_signals(context, text)
    ecommerce_hits = _keyword_hits(text, ECOMMERCE_KEYWORDS)
    if game_hits and len(game_hits) >= len(ecommerce_hits):
        return "game", {
            "method": "keyword_heuristic",
            "confidence": min(0.95, 0.55 + len(game_hits) * 0.08),
            "signals": game_hits,
            "fallback": False,
        }
    if ecommerce_hits:
        return "ecommerce", {
            "method": "keyword_heuristic",
            "confidence": min(0.92, 0.55 + len(ecommerce_hits) * 0.07),
            "signals": ecommerce_hits,
            "fallback": False,
        }

    return "ecommerce", {
        "method": "keyword_heuristic",
        "confidence": 0.4,
        "signals": [],
        "fallback": True,
        "reason": "No game or ecommerce keyword signal was strong enough; defaulted to ecommerce.",
    }


def _selected_work_order_type(context: Mapping[str, Any]) -> str | None:
    candidates = [
        context.get("work_order_type"),
        _nested_value(context.get("metadata_json"), "work_order_type"),
        _nested_value(context.get("structured_fields"), "work_order_type"),
        _nested_value(context.get("reviewed_fields"), "work_order_type"),
        _nested_value(context.get("work_order"), "work_order_type"),
        _nested_value(context.get("work_order"), "type"),
    ]
    for value in candidates:
        normalized = _normalize_work_order_type(value)
        if normalized:
            return normalized
    return None


def _normalize_work_order_type(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key in ("normalized_value", "value", "code", "id", "label", "name"):
            normalized = _normalize_work_order_type(value.get(key))
            if normalized:
                return normalized
        return None
    text = _string_value(value).strip().casefold()
    if not text:
        return None
    text = text.replace("-", "_").replace(" ", "_")
    aliases = {
        "ecommerce": "ecommerce",
        "e_commerce": "ecommerce",
        "commerce": "ecommerce",
        "shop": "ecommerce",
        "product": "ecommerce",
        "game": "game",
        "games": "game",
        "gaming": "game",
        "gambling": "gambling",
        "gambling_like": "gambling",
        "gambling_like_safe_game_ad": "gambling",
        "casino_safe": "gambling",
        "betting_safe": "gambling",
    }
    return aliases.get(text)


def _topic_angle_plan(vertical: str) -> list[dict[str, Any]]:
    if vertical == "gambling":
        return [
            {
                "slot": 1,
                "angle_type": "sky_rupture_spectacle",
                "purpose": (
                    "Test sky-scale pressure, flying boss or energy-source arrival, "
                    "divine light, feather meteor storm, and sky rupture."
                ),
                "avoid_repeating": ["dark_element_overload", "ancient_power_awakening"],
            },
            {
                "slot": 2,
                "angle_type": "dark_element_overload",
                "purpose": (
                    "Test dark entrance pressure, multi-element overload, space collapse, "
                    "and abstract power-vortex reveal."
                ),
                "avoid_repeating": ["sky_rupture_spectacle", "ancient_power_awakening"],
            },
            {
                "slot": 3,
                "angle_type": "ancient_power_awakening",
                "purpose": (
                    "Test ruins or temple guardian awakening, seal shatter, "
                    "golden particle burst, and mystery reveal climax."
                ),
                "avoid_repeating": ["sky_rupture_spectacle", "dark_element_overload"],
            },
        ]
    if vertical == "game":
        return [
            {
                "slot": 1,
                "angle_type": "challenge_failure",
                "purpose": "Test whether failure and challenge hooks drive curiosity.",
                "avoid_repeating": ["comeback_growth", "reward_burst"],
            },
            {
                "slot": 2,
                "angle_type": "comeback_growth",
                "purpose": "Test weak-to-strong or wrong-to-right progression.",
                "avoid_repeating": ["challenge_failure", "reward_burst"],
            },
            {
                "slot": 3,
                "angle_type": "reward_burst",
                "purpose": "Test visual satisfaction, rewards, upgrades, and payoff.",
                "avoid_repeating": ["challenge_failure", "comeback_growth"],
            },
        ]
    return [
        {
            "slot": 1,
            "angle_type": "pain_point",
            "purpose": "Test whether the audience recognizes the problem.",
            "avoid_repeating": ["scenario_resonance", "value_offer"],
        },
        {
            "slot": 2,
            "angle_type": "scenario_resonance",
            "purpose": "Test whether a daily-life scene creates self-recognition.",
            "avoid_repeating": ["pain_point", "value_offer"],
        },
        {
            "slot": 3,
            "angle_type": "value_offer",
            "purpose": "Test value, offer, or proof without unsupported claims.",
            "avoid_repeating": ["pain_point", "scenario_resonance"],
        },
    ]


def _copy_guidance(
    vertical: str,
    market_context: Mapping[str, Any],
    audience_lens: Mapping[str, Any],
) -> dict[str, Any]:
    if vertical == "game":
        hooks = ["Can you pass this challenge?", "Try again and level up.", "Unlock the reward."]
    elif vertical == "gambling":
        hooks = [
            "Something powerful has awakened.",
            "Choose carefully.",
            "Enter the unknown spectacle.",
        ]
    elif vertical == "ecommerce":
        hooks = [
            "Show the daily problem first.",
            "Connect the product to the routine.",
            "Use clear value without guaranteed outcomes.",
        ]
    else:
        hooks = [
            "Show the daily problem first.",
            "Connect the product to the routine.",
            "Use clear value without guaranteed outcomes.",
        ]
    return {
        "language": market_context.get("language"),
        "tone": "clear, specific, and culturally neutral",
        "primary_audience": {
            "gender": audience_lens.get("gender"),
            "age_range": audience_lens.get("age_range"),
        },
        "hook_directions": hooks,
    }


def _image_guidance(vertical: str) -> dict[str, Any]:
    if vertical == "game":
        return {
            "composition": "Use a gameplay or challenge-first visual with a clear payoff.",
            "avoid": ["real-money cues", "guaranteed reward claims", "fake platform UI"],
        }
    if vertical == "gambling":
        return {
            "composition": (
                "Use first/last-frame key art: first frame shows cleaned brand plus VIP "
                "and a strong boss or mysterious energy-source hook; last frame shows "
                "large cleaned brand plus VIP and CTA fully inside the safe area. "
                "Do not make door, gate, portal, or vault compositions the default."
            ),
            "avoid": [
                "casino props",
                "cash amounts",
                "wallet or balance UI",
                "guaranteed winning claims",
                "ordinary game battle or upgrade UI",
            ],
        }
    if vertical == "ecommerce":
        return {
            "composition": "Show a realistic product scenario and one clear benefit cue.",
            "avoid": ["medical before-after claims", "unverified proof", "overcrowded text"],
        }
    return {
        "composition": "Show a realistic product scenario and one clear benefit cue.",
        "avoid": ["medical before-after claims", "unverified proof", "overcrowded text"],
    }


def _video_guidance(vertical: str) -> dict[str, Any]:
    if vertical == "game":
        return {
            "duration_adaptive": True,
            "opening": "Lead with a visible challenge or failed attempt.",
            "middle": "Show progression, choice, or improvement.",
            "ending": "Resolve with reward, unlock, or next-action payoff.",
        }
    if vertical == "gambling":
        return {
            "duration_adaptive": True,
            "opening": (
                "0-3s: brand and VIP are visible with a mysterious boss or energy "
                "source hook; large brand typography is allowed if fully inside the "
                "safe area."
            ),
            "middle": (
                "3-9s: no large text; boss or energy source drives the spectacle. "
                "Combine 2-3 items from vfx_library into sky rupture, storm eye, "
                "energy throne, crystal core, light column, seal awakening, or "
                "abstract power-vortex beats."
            ),
            "ending": (
                "9-12s: resolve into cleaned brand plus VIP and CTA after a mystery "
                "reveal climax, keeping the full brand/VIP/CTA lockup complete."
            ),
            "forbidden_story_patterns": [
                "no combat plot",
                "no leveling",
                "no equipment upgrade",
                "no ordinary gameplay progression",
            ],
        }
    if vertical == "ecommerce":
        return {
            "duration_adaptive": True,
            "opening": "Lead with the audience problem or routine moment.",
            "middle": "Show product use and visible value cue.",
            "ending": "Close with offer, proof, or clear next step.",
        }
    return {
        "duration_adaptive": True,
        "opening": "Lead with the audience problem or routine moment.",
        "middle": "Show product use and visible value cue.",
        "ending": "Close with offer, proof, or clear next step.",
    }


def _gambling_creative_package() -> dict[str, Any]:
    return {
        "creative_package": "gambling_vfx_spectacle_package",
        "visual_language": "boss_or_mysterious_energy_source_as_vfx_driver",
        "core_formula": [
            "boss_or_mysterious_energy_source_arrival",
            "vfx_library_burst",
            "world_distortion",
            "mystery_reveal_climax",
            "cleaned_brand_vip_cta",
        ],
        "boss_matrix": GAMBLING_BOSS_MATRIX,
        "scene_pool": GAMBLING_SCENE_POOL,
        "reveal_mechanism_pool": GAMBLING_REVEAL_MECHANISM_POOL,
        "reveal_diversity_rule": (
            "Do not default every gambling creative to a physical door, gate, portal, "
            "or vault; at most one of the three gambling variants may use a physical "
            "door/gate/portal/vault composition. Prefer sky rupture, storm eye, energy "
            "throne, crystal core, golden light column, ancient seal awakening, or "
            "abstract power vortex for the other variants."
        ),
        "cta_pool": GAMBLING_CTA_POOL,
        "gambling_safety_rules": [
            "Boss or mysterious energy source is a VFX driver, not a combat character.",
            "Do not show fighting, leveling, equipment upgrades, or gameplay UI progression.",
            "Use curiosity, pressure, and mystery reveal instead of direct gambling mechanics.",
            "Middle 3-9s must be built from 2-3 VFX library items.",
            "Door, gate, portal, and vault imagery is optional and must not dominate all variants.",
        ],
    }


def _market_game_style_pack(
    *,
    context: Mapping[str, Any],
    market_context: Mapping[str, Any],
    audience_lens: Mapping[str, Any],
    vertical: str,
) -> dict[str, Any] | None:
    if vertical != "game":
        return None

    country_code = _string_value(market_context.get("country_code")) or "US"
    country_label = _string_value(market_context.get("country")) or country_code
    gender = _normalized_audience_label(audience_lens.get("gender"), fallback="All")
    age_range = _string_value(audience_lens.get("age_range")) or "All"
    age_bucket = _game_age_bucket(age_range)
    game_interest_hypothesis = _game_interest_hypothesis(age_bucket)
    gender_lens = _gender_game_lens(gender)
    if gender_lens:
        game_interest_hypothesis.append(gender_lens)

    preferred_game_archetypes = _preferred_game_archetypes(age_bucket)
    visual_world = _market_game_visual_world(country_code)
    gameplay_process = _gameplay_process(country_code, age_bucket)
    cultural_safety = _market_game_cultural_safety(country_code)

    return {
        "source": "system_inferred",
        "country_code": country_code,
        "country_label": country_label,
        "gender": gender,
        "age_range": age_range,
        "audience_summary": (
            f"Internal lens only: {country_label}, {gender}, {age_range}; adapt pacing, "
            "visual density, and challenge clarity without stating these traits in ad copy."
        ),
        "game_interest_hypothesis": _dedupe(game_interest_hypothesis),
        "preferred_game_archetypes": preferred_game_archetypes,
        "aaa_game_inspiration": {
            "genre_archetypes": preferred_game_archetypes[:3],
            "visual_language": [
                "AAA-style cinematic camera movement",
                "hero entrance into a high-detail fantasy arena",
                "boss-pressure encounter without gore",
                "skill burst VFX and mission-complete reward reveal",
            ],
            "must_avoid": [
                "licensed game names, logos, characters, or copied UI",
                "graphic violence or gore",
                "regulated gambling or monetary reward mechanics",
                "real religious figures, sacred content, or political claims",
            ],
        },
        "visual_world": visual_world,
        "gameplay_process": gameplay_process,
        "cultural_safety": cultural_safety,
        "confidence": "medium",
        "inference_basis": _market_game_inference_basis(context),
    }


def _game_age_bucket(age_range: str) -> str:
    numbers = _numbers_from_text(age_range)
    if not numbers:
        return "all"
    lower = numbers[0]
    upper = numbers[1] if len(numbers) > 1 else lower
    if lower <= 24 and upper <= 34:
        return "18-24"
    if lower <= 34 and upper <= 44:
        return "25-34"
    if lower >= 35:
        return "35+"
    return "all"


def _numbers_from_text(value: str) -> list[int]:
    numbers: list[int] = []
    current = ""
    for char in _string_value(value):
        if char.isdigit():
            current += char
            continue
        if current:
            numbers.append(int(current))
            current = ""
    if current:
        numbers.append(int(current))
    return numbers


def _game_interest_hypothesis(age_bucket: str) -> list[str]:
    if age_bucket == "18-24":
        return [
            "fast challenge and retry loop",
            "competitive achievement without outcome promises",
            "cinematic action energy",
        ]
    if age_bucket == "25-34":
        return [
            "progression mastery",
            "strategic choice and upgrade planning",
            "premium visual escape",
        ]
    if age_bucket == "35+":
        return [
            "clear rules and simple start",
            "low-friction progression",
            "readable reward reveal",
        ]
    return [
        "clear gameplay challenge",
        "simple player choice",
        "visible progress and unlock payoff",
    ]


def _gender_game_lens(gender: str) -> str:
    normalized = gender.strip().casefold()
    if normalized in {"male", "men", "man"}:
        return "bold mission pressure with high-contrast action pacing"
    if normalized in {"female", "women", "woman"}:
        return "stylish character agency with readable progression choices"
    return ""


def _preferred_game_archetypes(age_bucket: str) -> list[str]:
    if age_bucket == "18-24":
        return [
            "open-world action adventure",
            "cinematic RPG progression",
            "skill-based mission challenge",
        ]
    if age_bucket == "25-34":
        return [
            "cinematic RPG progression",
            "strategy adventure progression",
            "premium quest hub exploration",
        ]
    if age_bucket == "35+":
        return [
            "guided puzzle adventure",
            "clear mission progression",
            "light strategy challenge",
        ]
    return [
        "cinematic RPG progression",
        "mission-based adventure",
        "guided challenge run",
    ]


def _market_game_visual_world(country_code: str) -> list[str]:
    if country_code == "IN":
        return [
            "culture-inspired epic fantasy",
            "royal sandstone archway game portal",
            "festival-like gold lighting without sacred objects",
            "monsoon storm sky over a stylized fortress",
            "original fantasy guardian silhouette",
        ]
    if country_code == "SG":
        return [
            "sleek urban neon mission hub",
            "clean high-tech challenge arena",
            "rain-lit city depth with premium VFX",
        ]
    if country_code == "US":
        return [
            "large-scale cinematic mission world",
            "high-detail action arena",
            "comic-book energy without copied characters",
        ]
    return [
        "localized cinematic fantasy world",
        "high-detail challenge arena",
        "clear playable challenge transition for the final CTA",
    ]


def _gameplay_process(country_code: str, age_bucket: str) -> dict[str, Any]:
    if country_code == "IN":
        opening_conflict = (
            "a towering original guardian blocks the fortress gate with a timing challenge"
        )
        player_goal = "reach the glowing fortress gate and unlock the next arena"
    else:
        opening_conflict = "a high-pressure mission gate blocks progress"
        player_goal = "complete the challenge path and unlock the next arena"

    if age_bucket == "18-24":
        player_actions = [
            "dodge an energy wave",
            "choose the right skill",
            "retry the timing window",
            "chain a clean combo",
        ]
        progression_feedback = [
            "progress bar fills",
            "skill icon upgrades",
            "new path opens",
            "mission-complete flash",
        ]
    elif age_bucket == "25-34":
        player_actions = [
            "scan the arena route",
            "choose a strategy skill",
            "upgrade the hero loadout",
            "clear the mission gate",
        ]
        progression_feedback = [
            "power meter rises",
            "route marker unlocks",
            "new arena preview opens",
            "reward panel resolves into CTA",
        ]
    else:
        player_actions = [
            "tap to start",
            "follow a clear path",
            "make one readable choice",
            "complete the challenge",
        ]
        progression_feedback = [
            "step-by-step progress lights up",
            "next path opens",
            "unlock glow appears",
            "CTA panel settles cleanly",
        ]

    return {
        "player_goal": player_goal,
        "opening_conflict": opening_conflict,
        "player_actions": player_actions,
        "progression_feedback": progression_feedback,
        "ending_transition": (
            "camera races into the branded game world with a Start or Play Now CTA"
        ),
    }


def _market_game_cultural_safety(country_code: str) -> dict[str, list[str]]:
    if country_code == "IN":
        return {
            "allowed": [
                "India-inspired color, architecture, textile, and festival-lighting cues",
                "original fantasy guardians and symbolic light patterns",
                "fictional worldbuilding rather than religious depiction",
            ],
            "avoid": [
                "real deity names or real religious figures",
                "prayers, worship, sacrifices, or ritual reenactments",
                "scripture, mantras, sacred text, or religious claims",
                "caste, politics, or real community identity claims",
            ],
        }
    return {
        "allowed": [
            "local color, architecture, and entertainment cues as fictional worldbuilding",
            "original characters and symbolic light patterns",
        ],
        "avoid": [
            "real religious figures or sacred symbols",
            "political or real community identity claims",
            "licensed game IP, copied characters, logos, or UI",
        ],
    }


def _market_game_inference_basis(context: Mapping[str, Any]) -> list[str]:
    basis = ["country", "audience_lens", "game vertical classification"]
    text = _context_text(context)
    if "gaja" in text or "game_tld" in _game_signals(context, text):
        basis.append("game landing signal")
    if any(keyword in text for keyword in ("challenge", "level", "quest", "play")):
        basis.append("gameplay brief signal")
    return basis


def _normalized_audience_label(value: Any, *, fallback: str) -> str:
    text = _string_value(value).strip()
    return text if text else fallback


def _dedupe(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _compliance_guardrails(vertical: str) -> list[str]:
    guardrails = [
        "Do not claim guaranteed results.",
        "Do not invent local trending topics.",
        "Do not imply sensitive personal attributes.",
        "Avoid fake platform UI, fake endorsements, and unsupported proof.",
        "Keep claims aligned with provided landing page, brief, and work order context.",
    ]
    if vertical in {"game", "gambling"}:
        guardrails.extend(
            [
                (
                    "Keep rewards as abstract progress, unlock, spectacle, or next-action "
                    "feedback only."
                ),
                (
                    "Do not show or imply real-money gambling, deposit/recharge, "
                    "withdrawal, payout, cash value, wallet or balance UI, casino props, "
                    "slot machines, chips, roulette, dice, poker props, jackpot panels, "
                    "guaranteed winning, or guaranteed outcome claims."
                ),
            ]
        )
    if vertical == "gambling":
        guardrails.extend(
            [
                (
                    "Do not show cash amounts, Win Cash, Guaranteed Money, Guaranteed "
                    "Win, withdrawal/recharge/balance UI, wallet panels, payout panels, "
                    "casino tables, slot machines, dice, chips, roulette, or poker props."
                ),
                (
                    "For gambling creative package, Boss is a VFX source and entrance "
                    "opener only; avoid combat story, leveling, equipment, player upgrade, "
                    "or standard gameplay progression."
                ),
                (
                    "Use EPIC, LEGENDARY, BIG REWARD, Score, Points, Stars, or Power only "
                    "as weak abstract feedback without cash value or outcome promises."
                ),
            ]
        )
    return guardrails


def _context_text(context: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "raw_content",
        "structured_fields",
        "reviewed_fields",
        "product_name",
        "campaign_name",
        "audience_description",
        "landing_url",
        "landing_page",
        "work_order",
        "brief",
        "event_name",
        "country",
        "media",
    ):
        _collect_text(context.get(key), parts)
    return " ".join(parts).casefold()


def _game_signals(context: Mapping[str, Any], text: str) -> list[str]:
    signals = _keyword_hits(text, GAME_KEYWORDS)
    landing_url = _string_value(context.get("landing_url"))
    host = urlparse(landing_url).hostname or ""
    if host.rsplit(".", 1)[-1].casefold() == "game":
        signals.append("game_tld")
    return list(dict.fromkeys(signals))


def _nested_value(value: Any, target_key: str) -> Any:
    if not isinstance(value, Mapping):
        return None
    for key, item in value.items():
        if str(key).casefold() == target_key.casefold():
            return item
        nested = _nested_value(item, target_key)
        if nested not in (None, ""):
            return nested
    return None


def _normalize_country_code(value: Any) -> str | None:
    text = _string_value(value).strip().casefold()
    if not text:
        return None
    return COUNTRY_ALIASES.get(text)


def _first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, ""):
            return value
    return None


def _age_range(age_min: Any, age_max: Any) -> str:
    min_text = _string_value(age_min).strip()
    max_text = _string_value(age_max).strip()
    if min_text and max_text:
        return f"{min_text}-{max_text}"
    if min_text:
        return f"{min_text}+"
    return "All"


def _pain_points(context: Mapping[str, Any]) -> list[str]:
    text = _context_text(context)
    pain_points = ["attention fatigue"]
    if any(keyword in text for keyword in ("office", "busy", "routine", "work")):
        pain_points.append("work pressure")
    if any(keyword in text for keyword in ("humid", "skin", "skincare", "serum")):
        pain_points.append("daily skincare friction")
    return pain_points


def _buying_motivations(context: Mapping[str, Any]) -> list[str]:
    text = _context_text(context)
    motivations = ["practical convenience"]
    if any(keyword in text for keyword in ("office", "busy", "routine", "quality")):
        motivations.append("quality of life")
    if any(keyword in text for keyword in ("discount", "offer", "shop", "limited")):
        motivations.append("clear value")
    return motivations


def _keyword_hits(text: str, keywords: Sequence[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword in text]


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else str(value or "")
