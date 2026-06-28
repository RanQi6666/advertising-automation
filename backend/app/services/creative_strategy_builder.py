from __future__ import annotations

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
    "game lobby",
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
    "united states": "US",
    "usa": "US",
    "us": "US",
    "india": "IN",
    "in": "IN",
    "indonesia": "ID",
    "id": "ID",
    "malaysia": "MY",
    "my": "MY",
    "thailand": "TH",
    "th": "TH",
    "vietnam": "VN",
    "vn": "VN",
    "brazil": "BR",
    "br": "BR",
    "mexico": "MX",
    "mx": "MX",
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


def build_creative_strategy(
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
    return {
        "schema_version": CREATIVE_STRATEGY_SCHEMA_VERSION,
        "vertical": vertical,
        "classification": classification,
        "market_context": market_context,
        "audience_lens": audience_lens,
        "topic_angle_plan": topic_angle_plan,
        "copy_guidance": _copy_guidance(vertical, market_context, audience_lens),
        "image_guidance": _image_guidance(vertical),
        "video_guidance": _video_guidance(vertical),
        "compliance_guardrails": _compliance_guardrails(),
    }


def compact_creative_strategy(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = (
        "schema_version",
        "vertical",
        "classification",
        "market_context",
        "audience_lens",
        "topic_angle_plan",
        "copy_guidance",
        "image_guidance",
        "video_guidance",
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


def _classify_vertical(context: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    text = _context_text(context)
    game_hits = _keyword_hits(text, GAME_KEYWORDS)
    ecommerce_hits = _keyword_hits(text, ECOMMERCE_KEYWORDS)
    if len(game_hits) >= 2 and len(game_hits) >= len(ecommerce_hits):
        return "game", {
            "method": "keyword_heuristic",
            "confidence": min(0.95, 0.55 + len(game_hits) * 0.08),
            "signals": game_hits,
        }
    if len(ecommerce_hits) >= 2:
        return "ecommerce", {
            "method": "keyword_heuristic",
            "confidence": min(0.92, 0.55 + len(ecommerce_hits) * 0.07),
            "signals": ecommerce_hits,
        }
    return "unknown", {
        "method": "keyword_heuristic",
        "confidence": 0.45,
        "signals": game_hits + ecommerce_hits,
    }


def _topic_angle_plan(vertical: str) -> list[dict[str, Any]]:
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
    if vertical == "ecommerce":
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
    return [
        {
            "slot": 1,
            "angle_type": "scenario_resonance",
            "purpose": "Test a practical daily-life use case.",
            "avoid_repeating": ["benefit_demo", "trust_builder"],
        },
        {
            "slot": 2,
            "angle_type": "benefit_demo",
            "purpose": "Test a clear product benefit demonstration.",
            "avoid_repeating": ["scenario_resonance", "trust_builder"],
        },
        {
            "slot": 3,
            "angle_type": "trust_builder",
            "purpose": "Test credibility and low-risk next step.",
            "avoid_repeating": ["scenario_resonance", "benefit_demo"],
        },
    ]


def _copy_guidance(
    vertical: str,
    market_context: Mapping[str, Any],
    audience_lens: Mapping[str, Any],
) -> dict[str, Any]:
    if vertical == "game":
        hooks = ["Can you pass this challenge?", "Try again and level up.", "Unlock the reward."]
    elif vertical == "ecommerce":
        hooks = [
            "Show the daily problem first.",
            "Connect the product to the routine.",
            "Use clear value without guaranteed outcomes.",
        ]
    else:
        hooks = [
            "Start from a familiar use case.",
            "Show the practical benefit.",
            "Offer a low-risk next step.",
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
    if vertical == "ecommerce":
        return {
            "composition": "Show a realistic product scenario and one clear benefit cue.",
            "avoid": ["medical before-after claims", "unverified proof", "overcrowded text"],
        }
    return {
        "composition": "Show the product in a practical daily-use scenario.",
        "avoid": ["unsupported claims", "sensitive identity targeting", "fake testimonials"],
    }


def _video_guidance(vertical: str) -> dict[str, Any]:
    if vertical == "game":
        return {
            "duration_adaptive": True,
            "opening": "Lead with a visible challenge or failed attempt.",
            "middle": "Show progression, choice, or improvement.",
            "ending": "Resolve with reward, unlock, or next-action payoff.",
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
        "opening": "Lead with a recognizable situation.",
        "middle": "Demonstrate the product benefit.",
        "ending": "Close on a clear low-risk action.",
    }


def _compliance_guardrails() -> list[str]:
    return [
        "Do not claim guaranteed results.",
        "Do not invent local trending topics.",
        "Do not imply sensitive personal attributes.",
        "Avoid fake platform UI, fake endorsements, and unsupported proof.",
        "Keep claims aligned with provided landing page, brief, and work order context.",
    ]


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
