from collections.abc import Mapping, Sequence
from typing import Any

from backend.app.services.landing_visual_reference import (
    _is_gaja_url,
    build_landing_visual_reference,
    extract_landing_visual_reference,
)

GAJA_DOMAIN = "gaja777.game"
GAJA_TEMPLATE_ID = "gaja_brand"
MINI_GAME_POOL_TEMPLATE_ID = "mini_game_pool"

MINI_GAME_KEYWORDS = (
    "小游戏",
    "小游戏流量池",
    "小游戏合集",
    "mini game",
    "mini-game",
    "mini games",
    "game pool",
    "casual game",
)
GAJA_KEYWORDS = ("gaja", "gaja777", GAJA_DOMAIN)
COUNTRY_FIELD_KEYS = {
    "country",
    "country_code",
    "country_name",
    "target_country",
    "market",
    "geo",
    "region",
}
COUNTRY_CONTAINER_KEYS = {
    "work_order",
    "parsed_fields",
    "metadata",
    "context",
    "external_context",
}


def build_game_creative_strategy(context: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build a deterministic creative strategy for GAJA game ad work orders."""
    haystack = _context_text(context)
    landing_url = _string_value(context.get("landing_url"))
    product_name = _string_value(context.get("product_name"))

    if not _has_gaja_signal(haystack, landing_url, product_name):
        return None
    if _has_mini_game_signal(haystack):
        strategy = _mini_game_pool_strategy()
    else:
        strategy = _gaja_brand_strategy()
    country_style_pack = _country_style_pack(context, haystack)
    strategy = {
        **strategy,
        "country_style_pack": country_style_pack,
        "visual_concepts": _country_visual_concepts(
            country_style_pack,
            str(strategy.get("template_id") or ""),
        ),
        "text_layout_rules": _text_layout_rules(),
        "first_three_seconds": _first_three_seconds(country_style_pack),
    }
    visual_reference = extract_landing_visual_reference(context.get("landing_page"))
    if not visual_reference:
        visual_reference = build_landing_visual_reference(
            url=landing_url,
            metadata=context,
            title=product_name,
            text_excerpt=haystack,
        )
    if visual_reference:
        strategy = {**strategy, "landing_visual_reference": visual_reference}
    return strategy


def merge_creative_strategy(
    metadata: dict | None,
    context: Mapping[str, Any],
) -> dict:
    strategy = build_game_creative_strategy(context)
    if not strategy:
        return dict(metadata or {})
    return {**dict(metadata or {}), "creative_strategy": strategy}


def _gaja_brand_strategy() -> dict[str, Any]:
    video_recipe = {
        "duration_seconds": 12,
        "beats": [
            "0-2s: dark neon GAJA lobby hook with metallic GAJA logo and premium cards",
            "2-7s: fast carousel through original fantasy hero and jewel game cards",
            "7-10s: coherent glossy app lobby reveal matching landing page style",
            "10-12s: Start / Play Now low-text end card",
        ],
    }
    return {
        "template_id": GAJA_TEMPLATE_ID,
        "template_name": "GAJA premium neon game lobby ad",
        "duration_seconds": 12,
        "aspect_ratio": "9:16",
        "brand": {
            "display_name": "GAJA",
            "landing_domain": GAJA_DOMAIN,
            "palette": [
                "near-black navy",
                "electric cyan",
                "magenta violet glow",
                "orange CTA",
                "metallic gold highlight",
            ],
            "real_page_signals": [
                "metallic GAJA wordmark",
                "metallic GAJA logo",
                "premium neon GAJA brand emblem",
                "no visible numeric suffix",
                "no visible brand-number text",
                "dark premium mobile game lobby",
                "glossy rectangular game cards",
                "glossy black reflective floor",
                "orange Start button",
            ],
        },
        "meta_restricted_game_ad_safe_mode": True,
        "first_frame": {
            "role": "premium game-lobby hook poster",
            "visual_must_include": [
                "large metallic GAJA logo",
                "metallic GAJA wordmark",
                "premium neon GAJA brand emblem",
                "no visible numeric suffix",
                "no visible brand-number text",
                "dark premium mobile game lobby",
                "premium game cards angled in depth",
                "fantasy hero and jewel card gallery",
                "glossy black reflective floor",
                "metallic 3D brand styling",
                "cinematic neon rim light",
            ],
            "composition": (
                "Use a mature, high-contrast premium neon game lobby poster with glossy "
                "cards, cinematic depth, reflective black floor, and a metallic GAJA logo "
                "without any numeric suffix."
            ),
        },
        "last_frame": {
            "role": "conversion end card",
            "visual_must_include": [
                "premium neon game lobby",
                "metallic GAJA logo",
                "GAJA wordmark without numeric suffix",
                "Start / Play Now CTA",
                "orange CTA button matching the landing page",
                "clean phone start cue when appropriate",
            ],
            "cta_must_include": ["Start", "Play Now"],
            "composition": (
                "End on a clear low-text action card connected to the premium game lobby "
                "style with metallic GAJA branding and no visible numeric suffix."
            ),
        },
        "motion_direction": [
            "Start with a strong dark neon poster frame.",
            "Move through glossy fantasy and jewel game cards with energy transitions.",
            "Reveal the coherent premium neon GAJA lobby before the CTA.",
            "Hold the final Start or Play Now end card long enough to read.",
        ],
        "negative_style_cues": [
            "childlike puzzle blocks",
            "bubble-pop toys",
            "flat preschool cartoon style",
            "plain runner-game track",
            "generic falling-block game look",
            "restricted_review_props",
            "financial_prop_cues",
            "outcome_claim_cues",
        ],
        "compliance_guardrails": [
            "Use original game-card visuals inspired by the landing page style.",
            "Use low-text metallic GAJA branding without a numeric suffix.",
            "Do not show visible numeric suffix or visible brand-number text.",
            "Keep copy about navigation, variety, simple start, and app experience.",
            "Keep visuals broad-review friendly and non-regulated.",
            "Keep sensitive metadata on neutral labels only.",
            (
                "Use restricted_review_props, financial_prop_cues, and "
                "outcome_claim_cues for review-safe taxonomy."
            ),
            "Avoid fake Facebook or browser UI screenshots.",
        ],
        "video_recipe": video_recipe,
    }


def _mini_game_pool_strategy() -> dict[str, Any]:
    examples = [
        "Color Match",
        "Puzzle Dash",
        "Bubble Pop",
        "Tile Runner",
        "Quick Tap",
        "Word Quest",
        "Shape Switch",
    ]
    return {
        "template_id": MINI_GAME_POOL_TEMPLATE_ID,
        "template_name": "Mini-game pool to GAJA game hub ad",
        "duration_seconds": 12,
        "aspect_ratio": "9:16",
        "brand": {
            "display_name": "GAJA",
            "landing_domain": GAJA_DOMAIN,
            "palette": ["bright teal", "coral", "clean purple", "orange CTA"],
            "real_page_signals": [
                "casual game category tabs",
                "puzzle, runner, bubble, tile, and reaction game tiles",
                "metallic GAJA game hub end card",
                "no visible numeric suffix",
                "no visible brand-number text",
                "orange Start button",
            ],
        },
        "game_pool_examples": examples,
        "meta_restricted_game_ad_safe_mode": True,
        "first_frame": {
            "role": "gameplay click hook",
            "visual_must_include": [
                "one oversized casual mini-game challenge tile",
                "puzzle, runner, bubble, tile, or quick-tap challenge style",
                "small metallic GAJA corner logo without numeric suffix",
                "no visible numeric suffix",
                "no visible brand-number text",
                "short challenge hook such as Can you pass this level?",
            ],
            "composition": (
                "Lead with playable-looking challenge energy. Keep GAJA branding present "
                "but secondary until the final end card."
            ),
        },
        "last_frame": {
            "role": "GAJA game hub end card",
            "visual_must_include": [
                "metallic GAJA game hub with multiple mini-game tiles",
                "GAJA wordmark without numeric suffix",
                "casual category tabs",
                "Start / Play Now CTA",
                "orange CTA button matching the landing page",
            ],
            "cta_must_include": ["Start", "Play Now", "More Games"],
            "composition": (
                "Convert the mini-game hook into a broad low-text game collection. "
                "Make the final click target the metallic GAJA game hub."
            ),
        },
        "motion_direction": [
            "Open on a single simple game challenge.",
            "Use quick progress, level path, tap, or tile-swipe motion.",
            "Expand into a grid of casual games and finish on the metallic GAJA Start CTA.",
        ],
        "compliance_guardrails": [
            "Use Meta-safe casual-game visuals only.",
            "Use low-text metallic GAJA branding without a numeric suffix.",
            "Do not show visible numeric suffix or visible brand-number text.",
            "Use only puzzle, runner, bubble, tile, quick-tap, and category-tile visuals.",
            "Keep all copy about navigation, variety, simple start, and app experience.",
            "Keep visuals broad-review friendly and non-regulated.",
            "Avoid fake Facebook or browser UI screenshots.",
            "Use gameplay curiosity and game variety instead of claim-heavy wording.",
        ],
    }


def _country_style_pack(context: Mapping[str, Any], haystack: str) -> dict[str, Any]:
    country_text = " ".join(_country_values(context)).casefold()
    fallback_text = haystack.casefold()
    if _matches_country(
        country_text,
        fallback_text,
        exact_aliases=("\u5370\u5ea6", "india", "bharat", "in"),
        broad_aliases=("\u5370\u5ea6", "india", "bharat"),
    ):
        return {
            "country_code": "IN",
            "country_label": "India",
            "style_family": "epic Indian-inspired CG game world",
            "style_cues": [
                "original Indian epic guardian with mature heroic proportions",
                "golden wings framing a metallic GAJA wordmark",
                "mandala light geometry behind the hero silhouette",
                "royal palace archways with deep cinematic scale",
                "monsoon storm clouds and volumetric fire-gold light",
                "gold cinematic rim light over high-detail 3D surfaces",
            ],
            "cultural_safety_guardrails": [
                "Use original fantasy figures rather than real religious figures.",
                (
                    "Avoid worship scenes, sacred chants, exact deity names, political "
                    "flag focus, and real religious text."
                ),
                (
                    "Use culture-inspired geometry, fabric, archways, and lighting "
                    "as art direction only."
                ),
            ],
        }
    if _matches_country(
        country_text,
        fallback_text,
        exact_aliases=("\u7f8e\u56fd", "united states", "usa", "us", "america"),
        broad_aliases=("\u7f8e\u56fd", "united states", "usa", "america"),
    ):
        return {
            "country_code": "US",
            "country_label": "United States",
            "style_family": "cinematic American tech adventure CG game world",
            "style_cues": [
                "cinematic urban skyline with storm-lit hero scale",
                "neon tech arena with glass-and-metal depth",
                "space-grade game portal opening behind metallic GAJA",
                "road-trip horizon light beams and high-energy camera push",
                "premium esports-style lighting without team or league marks",
            ],
            "cultural_safety_guardrails": [
                "Use broad cinematic location cues rather than flags or politics.",
                "Avoid real brands, team marks, landmarks as the sole subject, and platform UI.",
            ],
        }
    return {
        "country_code": "GLOBAL",
        "country_label": "Global",
        "style_family": "global epic neon CG game world",
        "style_cues": [
            "original mythic guardian silhouette with cinematic scale",
            "neon portal geometry behind metallic GAJA",
            "storm-lit fantasy sky and premium 3D app-lobby depth",
            "high-detail reflective surfaces with controlled readable text space",
            "heroic game-world reveal without regulated props",
        ],
        "cultural_safety_guardrails": [
            "Use original fantasy culture-neutral motifs.",
            "Avoid real politics, real religious text, and sensitive symbols.",
        ],
    }


def _country_visual_concepts(
    style_pack: Mapping[str, Any],
    template_id: str,
) -> list[dict[str, Any]]:
    country_code = str(style_pack.get("country_code") or "GLOBAL")
    hub_phrase = (
        "metallic GAJA game hub"
        if template_id == MINI_GAME_POOL_TEMPLATE_ID
        else "metallic GAJA game-world lobby"
    )
    if country_code == "IN":
        return [
            {
                "variant_index": 1,
                "concept_id": "india_epic_guardian",
                "name": "Epic Guardian Ascent",
                "visual_theme": (
                    "original Indian epic guardian, golden wings, mandala light "
                    "geometry, monsoon storm clouds, gold cinematic rim light"
                ),
                "first_frame_visual": (
                    "original Indian epic guardian rises behind metallic GAJA, "
                    "golden wings open through mandala light geometry, monsoon storm "
                    "clouds and gold cinematic rim light create a mature CG hook"
                ),
                "last_frame_visual": (
                    f"{hub_phrase} revealed through the wing silhouette, safe orange "
                    "Start cue, deep CG depth, no numeric suffix"
                ),
                "motion_hint": "Fast push through storm light into a clean GAJA game-world reveal.",
                "text_angle": "GAJA plus Enter an Epic Game World plus Start Your Quest Now.",
            },
            {
                "variant_index": 2,
                "concept_id": "india_royal_portal",
                "name": "Royal Portal Reveal",
                "visual_theme": (
                    "palace archways, jewel-toned fabric motion, gold cinematic rim "
                    "light, deep royal corridor, premium 3D portal"
                ),
                "first_frame_visual": (
                    "towering palace archways open around metallic GAJA, jewel-toned "
                    "fabric motion and gold cinematic rim light create a precise epic "
                    "CG composition"
                ),
                "last_frame_visual": (
                    f"{hub_phrase} appears inside the royal portal with balanced safe "
                    "text space and a Start Your Quest Now cue"
                ),
                "motion_hint": (
                    "Camera flies through archways, light streaks lock into the GAJA mark."
                ),
                "text_angle": "GAJA plus Unlock a New Game Realm plus Play Now.",
            },
            {
                "variant_index": 3,
                "concept_id": "india_mythic_neon_lobby",
                "name": "Mythic Neon Lobby",
                "visual_theme": (
                    "mythic neon app lobby, mandala light geometry, sculpted hero "
                    "shadows, fire-gold edge light, premium game-world depth"
                ),
                "first_frame_visual": (
                    "mythic neon app lobby with metallic GAJA centered, mandala light "
                    "geometry and sculpted hero shadows create a sharp high-detail hook"
                ),
                "last_frame_visual": (
                    f"{hub_phrase} resolves into a premium neon game lobby with safe "
                    "CTA placement and no numeric suffix"
                ),
                "motion_hint": (
                    "Energy lines draw the mandala geometry, then transition into the lobby."
                ),
                "text_angle": "GAJA plus Enter the Game Realm plus Start.",
            },
        ]
    if country_code == "US":
        return [
            {
                "variant_index": 1,
                "concept_id": "us_cinematic_city_hero",
                "name": "Cinematic City Hero",
                "visual_theme": "cinematic urban skyline, storm-lit hero scale, metallic GAJA",
                "first_frame_visual": (
                    "cinematic urban skyline opens behind metallic GAJA, storm-lit "
                    "hero scale and glass reflections create a premium CG hook"
                ),
                "last_frame_visual": (
                    f"{hub_phrase} lands above a clean city-light horizon with a Start cue"
                ),
                "motion_hint": "Push from skyline light beams into the GAJA game-world surface.",
                "text_angle": "GAJA plus Enter a Bigger Game World plus Start.",
            },
            {
                "variant_index": 2,
                "concept_id": "us_neon_tech_arena",
                "name": "Neon Tech Arena",
                "visual_theme": "neon tech arena, glass-and-metal depth, precision CG light",
                "first_frame_visual": (
                    "neon tech arena surrounds metallic GAJA with glass-and-metal depth "
                    "and high-energy camera motion"
                ),
                "last_frame_visual": (
                    f"{hub_phrase} resolves inside the arena with safe centered text"
                ),
                "motion_hint": "Arena light tracks converge into the GAJA wordmark.",
                "text_angle": "GAJA plus Step Into the Arena plus Play Now.",
            },
            {
                "variant_index": 3,
                "concept_id": "us_space_game_world",
                "name": "Space Game World",
                "visual_theme": "space-grade game portal, orbital light arcs, metallic GAJA",
                "first_frame_visual": (
                    "space-grade game portal opens behind metallic GAJA, orbital light "
                    "arcs and deep black glass create a sharp CG poster frame"
                ),
                "last_frame_visual": (
                    f"{hub_phrase} emerges from the portal with controlled CTA space"
                ),
                "motion_hint": "Orbital arcs sweep toward the viewer before the GAJA reveal.",
                "text_angle": "GAJA plus Open a New Game World plus Explore.",
            },
        ]
    return [
        {
            "variant_index": 1,
            "concept_id": "global_epic_guardian",
            "name": "Epic Guardian Portal",
            "visual_theme": "original mythic guardian, neon portal geometry, metallic GAJA",
            "first_frame_visual": (
                "original mythic guardian silhouette stands behind metallic GAJA with "
                "storm-lit neon portal geometry"
            ),
            "last_frame_visual": f"{hub_phrase} resolves in a premium neon game world",
            "motion_hint": "Push through portal light into the GAJA game-world reveal.",
            "text_angle": "GAJA plus Enter an Epic Game World plus Start.",
        },
        {
            "variant_index": 2,
            "concept_id": "global_neon_portal",
            "name": "Neon Portal",
            "visual_theme": "premium neon portal, reflective black floor, metallic GAJA",
            "first_frame_visual": (
                "premium neon portal frames metallic GAJA with high-detail CG depth"
            ),
            "last_frame_visual": f"{hub_phrase} appears with a clean Start cue",
            "motion_hint": "Portal opens into the app-lobby reveal.",
            "text_angle": "GAJA plus Open the Game World plus Play Now.",
        },
        {
            "variant_index": 3,
            "concept_id": "global_mythic_lobby",
            "name": "Mythic Lobby",
            "visual_theme": "mythic neon lobby, heroic light beams, metallic GAJA",
            "first_frame_visual": "mythic neon lobby with metallic GAJA and heroic light beams",
            "last_frame_visual": f"{hub_phrase} resolves with controlled safe text space",
            "motion_hint": "Light beams sweep into a stable CTA frame.",
            "text_angle": "GAJA plus Start Your Quest Now.",
        },
    ]


def _text_layout_rules() -> dict[str, Any]:
    return {
        "max_visible_text_layers": 3,
        "allowed_visible_text": [
            "GAJA",
            "Enter an Epic Game World",
            "Start Your Quest Now",
        ],
        "banned_visible_text_policy": (
            "Follow the creative safety visible text hard ban; never render brand-number "
            "text or restricted-topic wording."
        ),
        "safe_area_width_pct": 86,
        "top_bottom_margin_pct": 10,
        "auto_fit": True,
        "line_limits": {
            "brand": 1,
            "headline": 2,
            "subheadline": 2,
        },
        "max_chars": {
            "brand": 8,
            "headline": 34,
            "subheadline": 44,
        },
        "layout_instruction": (
            "Keep GAJA, headline, and subheadline/CTA inside the central safe area; "
            "auto-fit text size, wrap cleanly, preserve at least 10% top/bottom "
            "margin, and allow no overflow outside the image or video frame."
        ),
    }


def _first_three_seconds(style_pack: Mapping[str, Any]) -> list[dict[str, str]]:
    country_label = str(style_pack.get("country_label") or "Global")
    return [
        {
            "time_range": "0-1s",
            "beat": f"Epic {country_label} CG visual shock with culture-inspired light geometry.",
            "visible_text": "GAJA",
        },
        {
            "time_range": "1-2s",
            "beat": "Metallic GAJA wordmark locks into frame with the headline.",
            "visible_text": "Enter an Epic Game World",
        },
        {
            "time_range": "2-3s",
            "beat": "Short subheadline or CTA appears, then transition into the game world.",
            "visible_text": "Start Your Quest Now",
        },
    ]


def _has_gaja_signal(haystack: str, landing_url: str, product_name: str) -> bool:
    if _is_gaja_url(landing_url):
        return True
    filtered_haystack = haystack
    normalized_landing_url = landing_url.lower().strip()
    if normalized_landing_url:
        filtered_haystack = filtered_haystack.replace(normalized_landing_url, " ")
    return any(keyword in filtered_haystack for keyword in GAJA_KEYWORDS) or (
        "gaja" in product_name.lower()
    )


def _has_mini_game_signal(haystack: str) -> bool:
    return any(keyword in haystack for keyword in MINI_GAME_KEYWORDS)


def _context_text(value: Any) -> str:
    parts: list[str] = []
    _collect_text(value, parts)
    return " ".join(parts).lower()


def _country_values(value: Any) -> list[str]:
    values: list[str] = []
    _collect_country_values(value, values)
    return values


def _collect_country_values(value: Any, values: list[str]) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().casefold()
            if normalized_key in COUNTRY_FIELD_KEYS:
                text = _string_value(item).strip()
                if text:
                    values.append(text)
                continue
            if normalized_key in COUNTRY_CONTAINER_KEYS:
                _collect_country_values(item, values)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            _collect_country_values(item, values)


def _matches_country(
    country_text: str,
    fallback_text: str,
    exact_aliases: tuple[str, ...],
    broad_aliases: tuple[str, ...],
) -> bool:
    normalized_country = f" {country_text.replace('-', ' ')} "
    for alias in exact_aliases:
        normalized_alias = alias.casefold()
        if normalized_alias in {"in", "us"}:
            if f" {normalized_alias} " in normalized_country:
                return True
            continue
        if normalized_alias in country_text:
            return True
    return any(alias.casefold() in fallback_text for alias in broad_aliases)


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


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else str(value or "")
