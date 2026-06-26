from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

BRAND_SAFETY_PROMPT_GUARDRAILS = "\n".join(
    (
        "Brand safety hard bans:",
        "- Do not create or preserve gambling, betting, casino, lottery, jackpot, "
        "odds, poker, roulette, slot machine, or chip references.",
        "- Do not create or preserve medicine, drug, pill, capsule, pharmacy, "
        "prescription, injection, treatment, cure, or medical-efficacy references.",
        "- Do not create or preserve money, currency, cash, coin, bank card, wallet, "
        "payment, price, profit, income, investment, cashback, dollar, RMB, or "
        "currency-symbol references.",
        "- Do not create or preserve price-advantage marketing language including "
        "discounts, low price, cheap, cheaper, free, sale, deal, promo, promotion, "
        "special price, save money, cost-effective, value-for-money, bargain, coupon, "
        "limited offer, or flash sale.",
        "- For visual assets, do not show cash, coins, currency symbols, bank cards, "
        "wallets, price tags, discount stickers, sale badges, coupons, chips, casinos, "
        "slot machines, pills, capsules, medicine bottles, pharmacies, or injection "
        "devices.",
        "- Rewrite risky operator input into neutral value language such as smoother "
        "experience, simpler setup, clearer content, daily use, quick start, stable "
        "and reliable, view details, try now, or learn more.",
    )
)

BRAND_SAFETY_VISUAL_BAN = (
    "Brand safety visual guidance: keep the scene neutral, compliance-safe, and "
    "suitable for broad ad review. Use product, lifestyle, or abstract visuals only. "
    "If the brief implies a restricted or high-risk category, replace it with a "
    "generic daily-use scene; avoid symbolic objects, badges, labels, or props that "
    "suggest regulated activity."
)

_POLICIES: dict[str, tuple[str, ...]] = {
    "gambling": (
        r"赌博",
        r"博彩",
        r"下注",
        r"投注",
        r"赔率",
        r"赌场",
        r"老虎机",
        r"轮盘",
        r"扑克",
        r"筹码",
        r"彩票",
        r"中奖",
        r"娱乐城",
        r"\bcasino\b",
        r"\bbet(?:ting)?\b",
        r"\bodds?\b",
        r"\bjackpot\b",
        r"\blottery\b",
        r"\bpoker\b",
        r"\broulette\b",
        r"\bslot\s*machine\b",
        r"\bchips?\b",
    ),
    "medicine": (
        r"药品",
        r"药物",
        r"处方药",
        r"药片",
        r"胶囊",
        r"注射器",
        r"药瓶",
        r"药房",
        r"治疗",
        r"疗效",
        r"治愈",
        r"降压",
        r"止痛",
        r"抗生素",
        r"\bmedicine\b",
        r"\bdrugs?\b",
        r"\bpills?\b",
        r"\bcapsules?\b",
        r"\bpharmac(?:y|ies)\b",
        r"\bprescription\b",
        r"\binjection\b",
        r"\btreatment\b",
        r"\bcure\b",
    ),
    "money": (
        r"现金",
        r"货币",
        r"硬币",
        r"钞票",
        r"银行卡",
        r"钱包",
        r"支付",
        r"付款",
        r"收款",
        r"价格",
        r"金额",
        r"收益",
        r"赚钱",
        r"利润",
        r"投资",
        r"返现",
        r"美元",
        r"人民币",
        r"[$￥¥€£]",
        r"\bmoney\b",
        r"\bcash\b",
        r"\bcoins?\b",
        r"\bcurrency\b",
        r"\bbank\s*cards?\b",
        r"\bwallets?\b",
        r"\bpayments?\b",
        r"\bprices?\b",
        r"\bprofits?\b",
        r"\bincome\b",
        r"\binvest(?:ment|ing)?\b",
        r"\bcash\s*back\b",
        r"\bcashback\b",
        r"\bdollars?\b",
        r"\brmb\b",
    ),
    "price_promotion": (
        r"折扣",
        r"低价",
        r"便宜",
        r"优惠",
        r"促销",
        r"满减",
        r"立减",
        r"特价",
        r"免费",
        r"低至",
        r"仅需",
        r"超值",
        r"划算",
        r"省钱",
        r"性价比",
        r"抢购",
        r"购买立减",
        r"\bdiscounts?\b",
        r"\blow\s*prices?\b",
        r"\bcheap(?:er|est)?\b",
        r"\bfree\b",
        r"\bsales?\b",
        r"\bdeals?\b",
        r"\bpromos?\b",
        r"\bpromotions?\b",
        r"\bspecial\s*prices?\b",
        r"\bsave\s*money\b",
        r"\bcost[-\s]*effective\b",
        r"\bvalue[-\s]*for[-\s]*money\b",
        r"\bbargains?\b",
        r"\bcoupons?\b",
        r"\blimited\s*offers?\b",
        r"\bflash\s*sales?\b",
    ),
}

_SUGGESTIONS = {
    "gambling": (
        "Rewrite around neutral product usage; remove gambling, betting, odds, casino, "
        "lottery, and chip references."
    ),
    "medicine": (
        "Rewrite around neutral usage experience; remove medicine, drug, pill, pharmacy, "
        "treatment, and cure references."
    ),
    "money": (
        "Rewrite around non-price value such as clarity, setup, daily use, stability, "
        "or experience."
    ),
    "price_promotion": (
        "Do not use discount, low-price, free, promo, sale, deal, coupon, or "
        "value-for-money language; emphasize experience instead."
    ),
}

_COMPILED_POLICIES = {
    category: tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)
    for category, patterns in _POLICIES.items()
}

_URL_FIELD_NAMES = {
    "url",
    "link",
    "asset_url",
    "material_url",
    "file_url",
    "image_url",
    "image_asset_url",
    "video_url",
    "video_asset_url",
    "cover_url",
}


def scan_brand_safety(payload: Any) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for field_path, text in _walk_text(payload):
        for category, patterns in _COMPILED_POLICIES.items():
            for pattern in patterns:
                match = pattern.search(text)
                if not match:
                    continue
                key = (category, field_path, match.group(0).lower())
                if key in seen:
                    break
                seen.add(key)
                findings.append(
                    {
                        "category": category,
                        "severity": "high",
                        "field_path": field_path,
                        "matched_text": match.group(0),
                        "suggestion": _SUGGESTIONS[category],
                    }
                )
                break

    return {
        "status": "blocked" if findings else "passed",
        "highest_severity": "high" if findings else None,
        "findings": findings,
    }


def _walk_text(value: Any, path: str = "$") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            child_path = f"{path}.{key_text}" if path else key_text
            if _is_url_path(child_path):
                continue
            yield from _walk_text(child, child_path)
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_text(child, f"{path}[{index}]")
        return

    if isinstance(value, str):
        text = value.strip()
        if text:
            yield path, text
        return

    if isinstance(value, (int, float, bool)):
        yield path, str(value)


def _is_url_path(path: str) -> bool:
    normalized = path.lower().replace("-", "_")
    last_segment = normalized.rsplit(".", maxsplit=1)[-1]
    return last_segment in _URL_FIELD_NAMES or last_segment.endswith("_url")
