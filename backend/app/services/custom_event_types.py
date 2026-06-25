import re
from typing import Any

CUSTOM_EVENT_TYPES = {
    "complete_registration": "COMPLETE_REGISTRATION",
    "purchase": "PURCHASE",
    "add_to_cart": "ADD_TO_CART",
    "initiated_checkout": "INITIATED_CHECKOUT",
    "search": "SEARCH",
    "add_payment_info": "ADD_PAYMENT_INFO",
    "first_recharge": "first_recharge",
}

CUSTOM_EVENT_LABELS = {
    "complete_registration": "\u5b8c\u6210\u6ce8\u518c",
    "purchase": "\u8d2d\u4e70",
    "add_to_cart": "\u52a0\u5165\u8d2d\u7269\u8f66",
    "initiated_checkout": "\u53d1\u8d77\u7ed3\u8d26",
    "search": "\u641c\u7d22\u884c\u4e3a",
    "add_payment_info": "\u6dfb\u52a0\u652f\u4ed8\u4fe1\u606f",
    "first_recharge": "\u9996\u5145",
}

COMPLETE_REGISTRATION_ALIASES = {"complete_registration", "register", "signup"}
LEAD_ALIASES = {"lead", "leads"}
PURCHASE_ALIASES = {"purchase", "shop", "shopping", "buy", "order", "sales"}
ADD_TO_CART_ALIASES = {"add_to_cart", "addtocart", "cart"}
INITIATED_CHECKOUT_ALIASES = {"initiated_checkout", "initiatedcheckout", "checkout"}
SEARCH_ALIASES = {"search"}
ADD_PAYMENT_INFO_ALIASES = {"add_payment_info", "addpaymentinfo", "paymentinfo"}
FIRST_RECHARGE_ALIASES = {"first_recharge", "firstrecharge"}


def custom_event_key(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None

    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text.lower())
    normalized = re.sub(r"[\s-]+", "_", text.strip().lower())

    if "\u6ce8\u518c" in text or compact in COMPLETE_REGISTRATION_ALIASES:
        return "complete_registration"
    if "\u7ebf\u7d22" in text or compact in LEAD_ALIASES:
        return "lead"
    if (
        any(keyword in text for keyword in ("\u8d2d\u4e70", "\u8d2d\u7269", "\u4e0b\u5355"))
        or compact in PURCHASE_ALIASES
    ):
        return "purchase"
    if (
        any(keyword in text for keyword in ("\u52a0\u5165\u8d2d\u7269\u8f66", "\u52a0\u8d2d"))
        or compact in ADD_TO_CART_ALIASES
    ):
        return "add_to_cart"
    if (
        any(
            keyword in text
            for keyword in ("\u53d1\u8d77\u7ed3\u8d26", "\u7ed3\u8d26", "\u7ed3\u7b97")
        )
        or compact in INITIATED_CHECKOUT_ALIASES
    ):
        return "initiated_checkout"
    if "\u641c\u7d22" in text or compact in SEARCH_ALIASES:
        return "search"
    if "\u652f\u4ed8\u4fe1\u606f" in text or compact in ADD_PAYMENT_INFO_ALIASES:
        return "add_payment_info"
    if "\u9996\u5145" in text or compact in FIRST_RECHARGE_ALIASES:
        return "first_recharge"
    return normalized or None


def custom_event_type(value: Any) -> str | None:
    key = custom_event_key(value)
    if key in LEAD_ALIASES:
        return CUSTOM_EVENT_TYPES["complete_registration"]
    if key in CUSTOM_EVENT_TYPES:
        return CUSTOM_EVENT_TYPES[key]
    return None


def custom_event_label(value: Any) -> str | None:
    key = custom_event_key(value)
    if key in LEAD_ALIASES:
        return CUSTOM_EVENT_LABELS["complete_registration"]
    if key in CUSTOM_EVENT_LABELS:
        return CUSTOM_EVENT_LABELS[key]
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
