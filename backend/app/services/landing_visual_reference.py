from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

GAJA_DOMAIN = "gaja777.game"

_GAJA_BASE_REFERENCE: dict[str, Any] = {
    "confidence": 0.8,
    "palette": [
        "near-black navy background",
        "electric cyan edge light",
        "magenta and violet glow",
        "orange CTA accent",
        "gold metallic title highlight",
    ],
    "surface_style": [
        "dark premium mobile game lobby",
        "metallic GAJA logo styling",
        "metallic GAJA wordmark styling",
        "cinematic neon rim light",
        "glossy black reflective environment",
        "orange CTA accent",
    ],
    "gameplay_moment_archetypes": [
        "visible challenge setup",
        "failed attempt and retry moment",
        "clear player choice cue",
        "progression or level-up feedback",
        "reward unlock payoff cue",
    ],
    "composition_cues": [
        "metallic GAJA logo visible in the first frame",
        "premium GAJA brand emblem as a supporting icon",
        "no visible numeric suffix or brand-number text",
        "phone-screen vertical lobby composition",
        "visible challenge, retry, and reward cues",
        "clear orange Start or Play Now CTA in final beat",
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
    "video_recipe": {
        "duration_seconds": 12,
        "beats": [
            "0-2s: dark neon GAJA lobby hook with metallic GAJA logo and visible challenge setup",
            "2-7s: show failed attempt, quick retry choice, or progression moment",
            "7-10s: reward unlock payoff cue in a coherent glossy app lobby",
            "10-12s: simple Start / Play Now CTA beat",
        ],
    },
}


def build_landing_visual_reference(
    url: str,
    metadata: Mapping[str, Any] | None = None,
    title: str | None = None,
    text_excerpt: str | None = None,
) -> dict[str, Any] | None:
    metadata = metadata if isinstance(metadata, Mapping) else {}
    reference_images = _reference_images(metadata.get("reference_images"))
    is_gaja = _is_gaja_url(url) or _has_gaja_text(title, text_excerpt)

    if not is_gaja:
        return None

    reference = deepcopy(_GAJA_BASE_REFERENCE)
    if reference_images:
        reference.update(
            {
                "source": "reference_image",
                "status": "analyzed",
                "confidence": 0.86,
                "reference_image_count": len(reference_images),
                "analysis_note": (
                    "Using operator-provided landing page screenshots as visual style anchors."
                ),
            }
        )
    else:
        reference.update({"source": "domain_fallback", "status": "fallback"})
    return reference


def extract_landing_visual_reference(
    context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(context, Mapping):
        return None

    direct = context.get("visual_reference")
    if isinstance(direct, dict):
        return direct

    extracted_data = context.get("extracted_data")
    if isinstance(extracted_data, Mapping):
        nested = extracted_data.get("visual_reference")
        if isinstance(nested, dict):
            return nested

    return None


def merge_landing_visual_reference(
    strategy: dict[str, Any] | None,
    landing_page_context: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(strategy, dict):
        return strategy

    reference = extract_landing_visual_reference(landing_page_context)
    if not reference:
        return strategy

    merged = deepcopy(strategy)
    merged["landing_visual_reference"] = reference
    return merged


def _is_gaja_url(url: str) -> bool:
    host = urlparse(str(url or "")).hostname
    if not host:
        return False
    host = host.lower()
    return host == GAJA_DOMAIN or host.endswith(f".{GAJA_DOMAIN}")


def _has_gaja_text(*values: str | None) -> bool:
    text = " ".join(value for value in values if value).lower()
    return "gaja" in text or "gaja777" in text


def _reference_images(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]
