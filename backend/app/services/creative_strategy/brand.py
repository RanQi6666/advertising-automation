from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

BRAND_POLICY_PACK = {
    "source": "universal_brand_policy",
    "visible_text_windows": ["0-3s", "9-12s"],
    "middle_window_rule": "low text; optional tiny brand mark only",
    "first_frame_rule": "show visible brand with the opening hook",
    "last_frame_rule": "resolve to visible brand plus CTA",
    "forbidden": [
        "Do not invent brand names.",
        "Do not crop or hide the visible brand.",
        "Do not let reference videos override the current work-order brand.",
    ],
}


def resolve_brand_profile(context: Mapping[str, Any]) -> dict[str, Any]:
    source_field, raw_name = _first_brand_value(context)
    visible_name = _clean_visible_brand(raw_name)
    return {
        "raw_name": raw_name,
        "visible_name": visible_name,
        "source_field": source_field,
        "digit_policy": "remove_digits_for_visible_brand",
    }


def _first_brand_value(context: Mapping[str, Any]) -> tuple[str, str]:
    candidates = (
        ("product_name", context.get("product_name")),
        ("project_name", context.get("project_name")),
        ("brand_name", context.get("brand_name")),
        ("campaign_name", context.get("campaign_name")),
        (
            "structured_fields.product_name",
            _nested_value(context.get("structured_fields"), "product_name"),
        ),
        (
            "structured_fields.project_name",
            _nested_value(context.get("structured_fields"), "project_name"),
        ),
        (
            "reviewed_fields.product_name",
            _nested_value(context.get("reviewed_fields"), "product_name"),
        ),
        (
            "reviewed_fields.project_name",
            _nested_value(context.get("reviewed_fields"), "project_name"),
        ),
        ("work_order.product_name", _nested_value(context.get("work_order"), "product_name")),
        ("work_order.project_name", _nested_value(context.get("work_order"), "project_name")),
        ("work_order.campaign_name", _nested_value(context.get("work_order"), "campaign_name")),
        ("landing_page.title", _nested_value(context.get("landing_page"), "title")),
    )
    for field, value in candidates:
        text = _string_value(value).strip()
        if text:
            return field, text
    return "unknown", ""


def _clean_visible_brand(value: str) -> str:
    cleaned = re.sub(r"\d+", "", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_|")
    return cleaned or value


def _nested_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return None


def _string_value(value: Any) -> str:
    if value is None:
        return ""
    return str(value)
