from __future__ import annotations

from typing import Any


def validate_creative_strategy(strategy: dict[str, Any]) -> dict[str, Any]:
    if strategy.get("vertical") == "gambling":
        required = ("boss_matrix", "scene_pool", "reveal_mechanism_pool", "cta_pool")
        missing = [key for key in required if not strategy.get(key)]
        if missing:
            strategy.setdefault("validation_warnings", []).append(
                f"Missing gambling package fields: {', '.join(missing)}"
            )
    return strategy
