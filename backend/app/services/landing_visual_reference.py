from collections.abc import Mapping
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

GAJA_DOMAIN = "gaja777.game"


def build_landing_visual_reference(
    url: str,
    metadata: Mapping[str, Any] | None = None,
    title: str | None = None,
    text_excerpt: str | None = None,
) -> dict[str, Any] | None:
    return None


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
