from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize_reference_signal_pack(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    compact = {str(key): item for key, item in value.items() if item not in (None, "", [])}
    return compact or None
