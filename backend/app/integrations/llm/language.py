from typing import Any

DEFAULT_OPERATOR_LANGUAGE = "Simplified Chinese"

COUNTRY_LANGUAGE_RULES: dict[str, dict[str, str]] = {
    "BR": {
        "label": "Brazilian Portuguese",
        "instruction": "Use Brazilian Portuguese for user-facing ad content.",
    },
    "ID": {
        "label": "Indonesian",
        "instruction": "Use Indonesian for user-facing ad content.",
    },
    "IN": {
        "label": "Hindi for broad India consumer ads",
        "instruction": (
            "Use Hindi for broad India consumer ad hooks by default. Use English only when "
            "the landing page or work order clearly uses English as the consumer-facing language."
        ),
    },
    "MX": {
        "label": "Spanish for Mexico",
        "instruction": "Use Mexican Spanish for user-facing ad content.",
    },
    "MY": {
        "label": "Malay or English for Malaysia",
        "instruction": (
            "Use Malay for broad Malaysia consumer ads. Use English only when the landing page "
            "or work order clearly uses English as the consumer-facing language."
        ),
    },
    "PH": {
        "label": "Filipino or English for the Philippines",
        "instruction": (
            "Use Filipino for broad Philippines consumer ads. Use English only when the landing "
            "page or work order clearly uses English as the consumer-facing language."
        ),
    },
    "SG": {
        "label": "English for Singapore",
        "instruction": "Use English for user-facing ad content.",
    },
    "TH": {
        "label": "Thai",
        "instruction": "Use Thai for user-facing ad content.",
    },
    "US": {
        "label": "English",
        "instruction": "Use English for user-facing ad content.",
    },
    "VN": {
        "label": "Vietnamese",
        "instruction": "Use Vietnamese for user-facing ad content.",
    },
}

COUNTRY_ALIASES = {
    "america": "US",
    "brazil": "BR",
    "brasil": "BR",
    "india": "IN",
    "indonesia": "ID",
    "malaysia": "MY",
    "mexico": "MX",
    "philippines": "PH",
    "singapore": "SG",
    "thailand": "TH",
    "united states": "US",
    "usa": "US",
    "vietnam": "VN",
    "印尼": "ID",
    "印度": "IN",
    "印度尼西亚": "ID",
    "墨西哥": "MX",
    "巴西": "BR",
    "新加坡": "SG",
    "泰国": "TH",
    "美国": "US",
    "菲律宾": "PH",
    "越南": "VN",
    "马来西亚": "MY",
}


def build_target_language_context(
    *,
    campaign: Any | None = None,
    signals: dict | None = None,
    context: dict | None = None,
    draft_metadata: dict | None = None,
) -> dict[str, str]:
    explicit_language = _explicit_language(signals, context, draft_metadata)
    if explicit_language:
        return _language_context(
            label=explicit_language,
            instruction=f"Use {explicit_language} for user-facing ad content.",
            country_code=(
                _country_code_from_sources(campaign, signals, context, draft_metadata) or ""
            ),
            source="explicit",
        )

    existing = _existing_language_context(draft_metadata)
    if existing:
        return existing

    country_code = _country_code_from_sources(campaign, signals, context, draft_metadata)
    rule = COUNTRY_LANGUAGE_RULES.get(country_code or "")
    if rule:
        return _language_context(
            label=rule["label"],
            instruction=rule["instruction"],
            country_code=country_code or "",
            source="country",
        )

    return _language_context(
        label="English",
        instruction=(
            "Infer the most suitable user-facing language from the landing page and work order. "
            "If no market language is clear, use English. Do not default to Chinese."
        ),
        country_code=country_code or "",
        source="fallback",
    )


def language_requirements_prompt() -> str:
    return (
        "Audience language requirements:\n"
        "- Use the target_language object from the user payload.\n"
        "- User-facing fields must use target_language.instruction and must not default to "
        "Chinese unless target_language explicitly says Chinese.\n"
        "- Operator-facing strategy fields may use Simplified Chinese so operators can review "
        "the plan quickly.\n"
        "- User-facing fields include topic.title, ad body, primary_text, headline, description, "
        "visible image text, video subtitles, and voiceover."
    )


def _language_context(
    label: str,
    instruction: str,
    country_code: str,
    source: str,
) -> dict[str, str]:
    return {
        "label": label,
        "instruction": instruction,
        "country_code": country_code,
        "source": source,
        "operator_language": DEFAULT_OPERATOR_LANGUAGE,
    }


def _explicit_language(
    signals: dict | None,
    context: dict | None,
    draft_metadata: dict | None,
) -> str:
    for source in (signals, context, draft_metadata):
        if not isinstance(source, dict):
            continue
        for key in ("target_language", "ad_language", "language"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                label = value.get("label") or value.get("instruction")
                if isinstance(label, str) and label.strip():
                    return label.strip()
    return ""


def _existing_language_context(draft_metadata: dict | None) -> dict[str, str] | None:
    if not isinstance(draft_metadata, dict):
        return None
    value = draft_metadata.get("target_language")
    if not isinstance(value, dict):
        return None
    label = value.get("label")
    instruction = value.get("instruction")
    if not isinstance(label, str) or not isinstance(instruction, str):
        return None
    return _language_context(
        label=label,
        instruction=instruction,
        country_code=str(value.get("country_code") or ""),
        source=str(value.get("source") or "metadata"),
    )


def _country_code_from_sources(
    campaign: Any | None,
    signals: dict | None,
    context: dict | None,
    draft_metadata: dict | None,
) -> str:
    candidates = [
        _country_from_mapping(signals),
        _country_from_mapping(context),
        _country_from_mapping(draft_metadata),
        _country_from_campaign(campaign),
    ]
    for candidate in candidates:
        code = _normalize_country_code(candidate)
        if code:
            return code
    return ""


def _country_from_campaign(campaign: Any | None) -> Any | None:
    if campaign is None:
        return None
    metadata = getattr(campaign, "metadata_json", None)
    return _country_from_mapping(metadata)


def _country_from_mapping(value: Any | None) -> Any | None:
    if not isinstance(value, dict):
        return None

    for key in ("country_code", "countries", "country"):
        candidate = value.get(key)
        if candidate:
            return candidate

    target_language = value.get("target_language")
    if isinstance(target_language, dict) and target_language.get("country_code"):
        return target_language.get("country_code")

    work_order = value.get("work_order")
    if isinstance(work_order, dict):
        direct = _country_from_mapping(work_order)
        if direct:
            return direct
        parsed_fields = work_order.get("parsed_fields")
        direct = _country_from_mapping(parsed_fields)
        if direct:
            return direct

    parsed_fields = value.get("parsed_fields")
    if isinstance(parsed_fields, dict):
        return _country_from_mapping(parsed_fields)

    adset = value.get("adset_payload")
    if isinstance(adset, dict):
        return _country_from_mapping(adset)

    return None


def _normalize_country_code(value: Any | None) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, (list, tuple)):
        for item in value:
            code = _normalize_country_code(item)
            if code:
                return code
        return ""
    if isinstance(value, dict):
        for key in ("country_code", "code", "normalized_value", "value", "label", "name"):
            code = _normalize_country_code(value.get(key))
            if code:
                return code
        return ""

    normalized = str(value).strip()
    if not normalized:
        return ""
    if len(normalized) == 2 and normalized.isascii() and normalized.isalpha():
        return normalized.upper()
    return COUNTRY_ALIASES.get(normalized.lower()) or COUNTRY_ALIASES.get(normalized) or ""
