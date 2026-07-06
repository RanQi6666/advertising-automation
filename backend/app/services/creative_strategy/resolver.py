from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

from backend.app.services.creative_strategy.brand import (
    BRAND_POLICY_PACK,
    resolve_brand_profile,
)
from backend.app.services.creative_strategy.packages import (
    attach_game_style_metadata,
    resolve_package_fields,
)
from backend.app.services.creative_strategy.reference import normalize_reference_signal_pack
from backend.app.services.creative_strategy.validators import validate_creative_strategy
from backend.app.services.creative_strategy_builder import _build_creative_strategy_base


def resolve_creative_strategy(
    context: Mapping[str, Any],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    strategy = _build_creative_strategy_base(context, today=today)
    strategy["brand_profile"] = resolve_brand_profile(context)
    strategy["brand_policy_pack"] = BRAND_POLICY_PACK

    package_fields = resolve_package_fields(strategy)
    strategy.update(package_fields)
    attach_game_style_metadata(strategy, package_fields)

    reference_signal_pack = normalize_reference_signal_pack(context.get("reference_signal_pack"))
    if reference_signal_pack:
        strategy["reference_signal_pack"] = reference_signal_pack

    return validate_creative_strategy(strategy)
