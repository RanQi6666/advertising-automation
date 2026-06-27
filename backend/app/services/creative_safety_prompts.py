from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

CREATIVE_VISIBLE_TEXT_BAN = (
    "Visible text hard ban: do not show or request visible text containing 777, "
    "Luck, \u8d62\u94b1, \u63d0\u73b0, \u91d1\u5e01\u96e8, "
    "\u8d4c\u573a\u684c\u9762, jackpot, casino, cash, coin, wallet, payout, "
    "withdraw, bonus, or balance."
)

CREATIVE_VISUAL_PROP_BAN = (
    "Visual prop hard ban: no casino tables, card-table layouts, chips, roulette, "
    "slot machines, dice, cash, coins, money rain, wallets, bank cards, payout buttons, "
    "balance counters, jackpot panels, withdrawal UI, or gambling-like reward effects."
)

CREATIVE_LOW_TEXT_DIRECTION = (
    "Use a low-text or no-text visual style. Express brand presence through a metallic "
    "GAJA logo or GAJA wordmark, premium neon app-lobby shapes, and "
    "game-card silhouettes; use no visible numeric suffix and no visible brand-number text."
)

CREATIVE_SAFE_CTA = (
    "CTA text should stay limited to safe words such as Start, Play Now, Join, or Explore."
)

CREATIVE_SAFETY_FALLBACK = (
    "low-text premium neon app lobby with metallic GAJA logo, glossy game-card "
    "silhouettes, no visible numeric suffix, and no visible brand-number text"
)

CREATIVE_SAFETY_PAYLOAD_PRESERVE_KEYS = frozenset(
    {
        "id",
        "campaign_id",
        "draft_id",
        "topic_id",
        "asset_id",
        "source_asset_ids",
        "selected_asset_ids",
    }
)

CREATIVE_SAFETY_RISK_TERMS = (
    "777",
    "luck",
    "\u8d62\u94b1",
    "\u63d0\u73b0",
    "\u91d1\u5e01\u96e8",
    "\u8d4c\u573a\u684c\u9762",
    "casino",
    "casino table",
    "card table",
    "gambling",
    "betting",
    "poker",
    "roulette",
    "slot",
    "chip",
    "cash",
    "coin",
    "money rain",
    "wallet",
    "bank card",
    "payout",
    "withdraw",
    "withdrawal",
    "jackpot",
    "balance",
    "bonus",
)

_REPLACEMENTS = (
    (r"https?://[^\s\"']*gaja\s*777\.game[^\s\"']*", "GAJA landing page"),
    (r"\bgaja\s*777\.game\b", "GAJA landing page"),
    (r"\bGAJA\s*777\b", "GAJA"),
    (r"\b777\b", "GAJA logo"),
    (r"\bLuck\b", "playful app label"),
    (r"\bcasino\s*tables?\b", "premium app-lobby surface"),
    (r"\bcard[-\s]*tables?\b", "premium app-lobby surface"),
    (r"\bcasino\b", "premium app lobby"),
    (r"\bgambling\b", "game entertainment"),
    (r"\bbet(?:ting)?\b", "game interaction"),
    (r"\bpoker\b", "original game-card art"),
    (r"\broulette\b", "neon radial accent"),
    (r"\bslot(?:\s*machine)?s?\b", "original game-card carousel"),
    (r"\bchips?\b", "glossy app tokens"),
    (r"\bcash\b", "neon highlight"),
    (r"\bcoins?\b", "neon particles"),
    (r"\bmoney\s*rain\b", "neon particle trail"),
    (r"\bwallets?\b", "simple start panel"),
    (r"\bbank\s*cards?\b", "clean app panel"),
    (r"\bpayout\b", "start action"),
    (r"\bwithdraw(?:al)?\b", "start action"),
    (r"\bjackpot\b", "feature highlight"),
    (r"\bbalances?\b", "status indicator"),
    (r"\bbonus(?:es)?\b", "feature highlight"),
    (r"\bregister\b", "Start"),
    (r"\bregistration\b", "quick start"),
    ("\u8d62\u94b1", "progress moment"),
    ("\u63d0\u73b0", "start action"),
    ("\u91d1\u5e01\u96e8", "neon particle trail"),
    ("\u8d4c\u573a\u684c\u9762", "premium app-lobby surface"),
)


def creative_safety_prompt_block() -> str:
    return "\n".join(
        (
            "Creative safety hard rules:",
            CREATIVE_VISIBLE_TEXT_BAN,
            CREATIVE_VISUAL_PROP_BAN,
            CREATIVE_LOW_TEXT_DIRECTION,
            CREATIVE_SAFE_CTA,
        )
    )


def contains_creative_safety_risk(value: str) -> bool:
    normalized = str(value or "").casefold()
    return any(term in normalized for term in CREATIVE_SAFETY_RISK_TERMS)


def sanitize_creative_safety_text(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    for source, target in _REPLACEMENTS:
        flags = 0 if source.startswith("\\u") else re.IGNORECASE
        text = re.sub(source, target, text, flags=flags)
    return CREATIVE_SAFETY_FALLBACK if contains_creative_safety_risk(text) else text


def sanitize_creative_safety_payload(value: Any) -> Any:
    """Sanitize prompt-facing payloads without changing exact local identifiers."""
    if isinstance(value, str):
        return sanitize_creative_safety_text(value)
    if isinstance(value, Mapping):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            if str(key) in CREATIVE_SAFETY_PAYLOAD_PRESERVE_KEYS:
                sanitized[key] = item
            else:
                sanitized[key] = sanitize_creative_safety_payload(item)
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [sanitize_creative_safety_payload(item) for item in value]
    return value
