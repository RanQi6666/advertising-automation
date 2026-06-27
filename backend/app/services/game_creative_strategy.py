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
            "0-2s: dark neon app-lobby hook with abstract G mark and premium cards",
            "2-7s: fast carousel through original fantasy and jewel game cards",
            "7-10s: coherent app lobby reveal matching landing page style",
            "10-12s: Start / Play Now low-text end card",
        ],
    }
    return {
        "template_id": GAJA_TEMPLATE_ID,
        "template_name": "GAJA premium neon game lobby ad",
        "duration_seconds": 12,
        "aspect_ratio": "9:16",
        "brand": {
            "display_name": "G app",
            "landing_domain": GAJA_DOMAIN,
            "palette": [
                "near-black navy",
                "electric cyan",
                "magenta violet glow",
                "orange CTA",
                "metallic gold highlight",
            ],
            "real_page_signals": [
                "abstract G mark",
                "no visible brand-number text",
                "dark premium mobile game lobby",
                "glossy rectangular game cards",
                "orange Start button",
            ],
        },
        "meta_restricted_game_ad_safe_mode": True,
        "first_frame": {
            "role": "premium game-lobby hook poster",
            "visual_must_include": [
                "large abstract G mark",
                "no visible brand-number text",
                "dark premium mobile game lobby",
                "premium game cards angled in depth",
                "metallic 3D title styling",
                "cinematic neon rim light",
            ],
            "composition": (
                "Use a mature, high-contrast premium neon game lobby poster with glossy "
                "cards, cinematic depth, and only an abstract G mark for brand presence."
            ),
        },
        "last_frame": {
            "role": "conversion end card",
            "visual_must_include": [
                "premium neon game lobby",
                "abstract G mark",
                "Start / Play Now CTA",
                "orange CTA button matching the landing page",
                "clean phone registration cue when appropriate",
            ],
            "cta_must_include": ["Start", "Play Now"],
            "composition": (
                "End on a clear low-text action card connected to the premium game lobby "
                "style without showing brand-number text."
            ),
        },
        "motion_direction": [
            "Start with a strong dark neon poster frame.",
            "Move through glossy fantasy and jewel game cards with energy transitions.",
            "Reveal the coherent premium neon game lobby before the CTA.",
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
            "Use low-text or no-text branding with an abstract G mark.",
            "Do not show visible brand-number text.",
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
            "display_name": "G app",
            "landing_domain": GAJA_DOMAIN,
            "palette": ["bright teal", "coral", "clean purple", "orange CTA"],
            "real_page_signals": [
                "casual game category tabs",
                "puzzle, runner, bubble, tile, and reaction game tiles",
                "abstract G mark game hub end card",
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
                "light abstract G corner icon",
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
                "abstract G game hub with multiple mini-game tiles",
                "casual category tabs",
                "Start / Play Now CTA",
                "orange CTA button matching the landing page",
            ],
            "cta_must_include": ["Start", "Play Now", "More Games"],
            "composition": (
                "Convert the mini-game hook into a broad low-text game collection. "
                "Make the final click target the abstract G game hub."
            ),
        },
        "motion_direction": [
            "Open on a single simple game challenge.",
            "Use quick progress, level path, tap, or tile-swipe motion.",
            "Expand into a grid of casual games and finish on the abstract G Start CTA.",
        ],
        "compliance_guardrails": [
            "Use Meta-safe casual-game visuals only.",
            "Use low-text or no-text branding with an abstract G mark.",
            "Do not show visible brand-number text.",
            "Use only puzzle, runner, bubble, tile, quick-tap, and category-tile visuals.",
            "Keep all copy about navigation, variety, simple start, and app experience.",
            "Keep visuals broad-review friendly and non-regulated.",
            "Avoid fake Facebook or browser UI screenshots.",
            "Use gameplay curiosity and game variety instead of claim-heavy wording.",
        ],
    }


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
