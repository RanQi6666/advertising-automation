from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

INDIA_COUNTRY_OVERLAY = {
    "overlay_id": "country/overlays/IN",
    "country_code": "IN",
    "preferred_boss_groups": [
        "sky_rupture_spectacle",
        "ancient_power_awakening",
    ],
    "preferred_scenes": [
        "golden_sanctum",
        "sandstone_festival_city",
        "sky_temple",
    ],
    "preferred_reveal_mechanisms": [
        "golden_light_column",
        "ancient_seal_awakening",
        "storm_eye",
        "crystal_core",
    ],
    "visual_bias": [
        "gold lighting",
        "festival-light atmosphere without sacred objects",
        "sandstone fantasy architecture",
    ],
    "avoid": [
        "real deity names or real religious figures",
        "prayers, worship, sacrifices, or ritual reenactments",
        "scripture, mantras, sacred text, or religious claims",
        "caste, politics, or real community identity claims",
    ],
}

DEFAULT_COUNTRY_OVERLAY = {
    "overlay_id": "default/country_overlay",
    "country_code": "default",
    "preferred_boss_groups": [],
    "preferred_scenes": [],
    "preferred_reveal_mechanisms": [],
    "visual_bias": ["localized fictional worldbuilding"],
    "avoid": [
        "real religious figures or sacred symbols",
        "political or real community identity claims",
        "licensed IP, copied characters, logos, or UI",
    ],
}


def resolve_package_fields(strategy: Mapping[str, Any]) -> dict[str, Any]:
    vertical = str(strategy.get("vertical") or "")
    market_context_value = strategy.get("market_context")
    market_context = market_context_value if isinstance(market_context_value, Mapping) else {}
    country_code = str(market_context.get("country_code") or "US")
    if vertical == "gambling":
        return _gambling_package_fields(country_code)
    if vertical == "game":
        return _game_package_fields(country_code)
    if vertical == "ecommerce":
        return {
            "style_pack_id": "ecommerce/default/product_benefit",
            "style_pack": {
                "style_pack_id": "ecommerce/default/product_benefit",
                "base_pack_id": "ecommerce/base/product_benefit",
                "country_overlay_id": _country_overlay(country_code)["overlay_id"],
            },
            "country_overlay": _country_overlay(country_code),
        }
    return {}


def attach_game_style_metadata(strategy: dict[str, Any], package_fields: Mapping[str, Any]) -> None:
    if strategy.get("vertical") != "game":
        return
    market_pack = strategy.get("market_game_style_pack")
    if not isinstance(market_pack, dict):
        return
    market_pack["style_pack_id"] = package_fields.get("style_pack_id")
    market_pack["boss_guidance"] = deepcopy(package_fields.get("boss_guidance"))


def _gambling_package_fields(country_code: str) -> dict[str, Any]:
    overlay = _country_overlay(country_code)
    style_pack_id = (
        "gambling/IN/vfx_spectacle_current"
        if country_code == "IN"
        else "gambling/default/vfx_spectacle"
    )
    return {
        "style_pack_id": style_pack_id,
        "style_pack": {
            "style_pack_id": style_pack_id,
            "base_pack_id": "gambling/base/vfx_spectacle",
            "country_overlay_id": overlay["overlay_id"],
            "composition": "base_style_pack_plus_country_overlay",
        },
        "country_overlay": overlay,
        "boss_guidance": {
            "role": "vfx pressure source",
            "must_show": [
                "boss or mysterious energy-source arrival",
                "VFX-driven pressure and reveal",
                "brand and CTA resolution",
            ],
            "must_avoid": [
                "combat story",
                "player leveling",
                "equipment upgrades",
                "real-money gambling mechanics",
            ],
        },
    }


def _game_package_fields(country_code: str) -> dict[str, Any]:
    overlay = _country_overlay(country_code)
    style_pack_id = (
        "game/IN/boss_challenge_fantasy"
        if country_code == "IN"
        else "game/default/cinematic_mission"
    )
    return {
        "style_pack_id": style_pack_id,
        "style_pack": {
            "style_pack_id": style_pack_id,
            "base_pack_id": "game/base/cinematic_mission",
            "country_overlay_id": overlay["overlay_id"],
            "composition": "gameplay_style_pack_plus_country_overlay",
        },
        "country_overlay": overlay,
        "boss_guidance": {
            "role": "playable challenge obstacle",
            "must_show": [
                "player action",
                "readable challenge rule",
                "retry or choice",
                "progress feedback",
            ],
            "must_avoid": [
                "real-money gambling mechanics",
                "copied game characters",
                "graphic violence or gore",
            ],
        },
    }


def _country_overlay(country_code: str) -> dict[str, Any]:
    if country_code == "IN":
        return deepcopy(INDIA_COUNTRY_OVERLAY)
    overlay = deepcopy(DEFAULT_COUNTRY_OVERLAY)
    overlay["country_code"] = country_code
    return overlay
