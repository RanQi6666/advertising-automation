import copy
import hashlib
import re
from collections import OrderedDict
from time import monotonic

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.work_order import WorkOrder
from backend.app.integrations.llm.factory import get_llm_provider
from backend.app.schemas.work_order import (
    WorkOrderCreate,
    WorkOrderDeliveryExtractionRead,
)
from backend.app.services.custom_event_types import (
    custom_event_key,
    custom_event_label,
    custom_event_type,
)
from backend.app.services.work_order_parser import parse_work_order_text

DELIVERY_FIELD_KEYS = (
    "landing_url",
    "event_name",
    "country",
    "age_min",
    "age_max",
    "gender",
    "audience_description_raw",
)
DELIVERY_FIELD_STATUSES = {"extracted", "suggested", "missing", "conflict"}
DELIVERY_EXTRACTION_CACHE_SCHEMA = "ad_delivery_extract_v1"
DELIVERY_EXTRACTION_CACHE_MAX_SIZE = 128
DELIVERY_EXTRACTION_CACHE_TTL_SECONDS = 60 * 60
COUNTRY_ALIASES = {
    "印度": "印度",
    "india": "印度",
    "in": "印度",
    "美国": "美国",
    "usa": "美国",
    "us": "美国",
    "united states": "美国",
    "菲律宾": "菲律宾",
    "philippines": "菲律宾",
    "印尼": "印度尼西亚",
    "印度尼西亚": "印度尼西亚",
    "indonesia": "印度尼西亚",
    "泰国": "泰国",
    "thailand": "泰国",
    "越南": "越南",
    "vietnam": "越南",
    "马来西亚": "马来西亚",
    "malaysia": "马来西亚",
    "新加坡": "新加坡",
    "singapore": "新加坡",
    "巴西": "巴西",
    "brazil": "巴西",
    "墨西哥": "墨西哥",
    "mexico": "墨西哥",
}
COUNTRY_CODES = {
    "印度": "IN",
    "美国": "US",
    "菲律宾": "PH",
    "印度尼西亚": "ID",
    "泰国": "TH",
    "越南": "VN",
    "马来西亚": "MY",
    "新加坡": "SG",
    "巴西": "BR",
    "墨西哥": "MX",
}

_DELIVERY_EXTRACTION_CACHE: OrderedDict[str, tuple[float, dict]] = OrderedDict()


class WorkOrderService:
    async def extract_delivery_fields(self, raw_content: str) -> WorkOrderDeliveryExtractionRead:
        local_extraction = _extract_delivery_fields_locally(raw_content)
        if _can_use_local_delivery_extraction(local_extraction):
            return local_extraction

        provider = get_llm_provider()
        cache_key = _delivery_extraction_cache_key(raw_content, provider)
        cached = _get_cached_delivery_extraction(cache_key)
        if cached:
            return cached

        result = await provider.extract_delivery_fields(raw_content)
        extraction = _normalize_delivery_extraction(result)
        _set_cached_delivery_extraction(cache_key, extraction.model_dump(mode="json"))
        return extraction

    async def create_work_order(
        self,
        session: AsyncSession,
        payload: WorkOrderCreate,
    ) -> WorkOrder:
        parsed_fields = parse_work_order_text(payload.raw_content)
        reviewed_fields = payload.reviewed_delivery_fields or {}
        llm_fields = payload.llm_delivery_fields or {}
        metadata_json = {
            **(payload.metadata_json or {}),
            "llm_delivery_fields": llm_fields,
            "reviewed_delivery_fields": reviewed_fields,
        }
        reviewed_landing_url = _reviewed_text(reviewed_fields, "landing_url")
        reviewed_event_name = _reviewed_text(reviewed_fields, "event_name")
        reviewed_country = _reviewed_text(reviewed_fields, "country")
        reviewed_audience = _build_audience_description(reviewed_fields)

        work_order = WorkOrder(
            raw_content=payload.raw_content,
            parsed_fields=parsed_fields,
            project_name=parsed_fields.get("project_name"),
            country=reviewed_country or parsed_fields.get("country"),
            media=parsed_fields.get("media"),
            event_name=reviewed_event_name or parsed_fields.get("event_name"),
            product_name=parsed_fields.get("product_name"),
            audience_description=reviewed_audience or parsed_fields.get("audience_description"),
            landing_url=reviewed_landing_url or parsed_fields.get("landing_url"),
            report_timezone=parsed_fields.get("report_timezone"),
            metadata_json=metadata_json,
        )
        session.add(work_order)
        await session.commit()
        await session.refresh(work_order)
        return work_order

    async def list_work_orders(
        self,
        session: AsyncSession,
        limit: int,
        offset: int,
    ) -> list[WorkOrder]:
        result = await session.execute(
            select(WorkOrder).order_by(WorkOrder.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())


def _normalize_delivery_extraction(data: dict) -> WorkOrderDeliveryExtractionRead:
    raw_fields = data.get("fields") if isinstance(data, dict) else {}
    if not isinstance(raw_fields, dict):
        raw_fields = {}

    fields = {
        key: _normalize_delivery_field(raw_fields.get(key), key)
        for key in DELIVERY_FIELD_KEYS
    }
    review = data.get("review") if isinstance(data, dict) else {}
    if not isinstance(review, dict):
        review = {}

    review = {
        **review,
        "must_confirm": _text_list(review.get("must_confirm")) or [
            "landing_url",
            "event_name",
            "country",
        ],
        "missing_fields": _fields_with_status(fields, "missing"),
        "conflict_fields": _fields_with_status(fields, "conflict"),
        "suggested_fields": _fields_with_status(fields, "suggested"),
    }
    return WorkOrderDeliveryExtractionRead.model_validate(
        {
            "schema_version": data.get("schema_version") or "ad_delivery_extract_v1",
            "fields": fields,
            "review": review,
        }
    )


def _extract_delivery_fields_locally(raw_content: str) -> WorkOrderDeliveryExtractionRead:
    parsed_fields = parse_work_order_text(raw_content)
    url_candidates = _find_urls(raw_content)
    country_candidates = _find_country_candidates(raw_content, parsed_fields)
    event_value, event_status, event_reason = _find_event(raw_content, parsed_fields)
    age_min, age_max = _find_age_range(raw_content, parsed_fields)
    gender_value, gender_status, gender_reason = _find_gender(raw_content, parsed_fields)
    audience_raw = _find_audience_raw(raw_content, parsed_fields, age_min, age_max, gender_value)

    data = {
        "schema_version": DELIVERY_EXTRACTION_CACHE_SCHEMA,
        "fields": {
            "landing_url": _local_field(
                value=url_candidates[0] if len(url_candidates) == 1 else None,
                normalized_value=url_candidates[0] if len(url_candidates) == 1 else None,
                status=(
                    "extracted"
                    if len(url_candidates) == 1
                    else "conflict"
                    if len(url_candidates) > 1
                    else "missing"
                ),
                confidence=0.98 if len(url_candidates) == 1 else 0.55 if url_candidates else 0,
                evidence=url_candidates[:3],
                candidates=url_candidates,
                reason=(
                    "本地规则识别到唯一投放链接。"
                    if len(url_candidates) == 1
                    else "本地规则识别到多个链接，需要人工确认。"
                    if len(url_candidates) > 1
                    else "本地规则未识别到投放链接。"
                ),
            ),
            "event_name": _local_field(
                value=event_value,
                normalized_value=_normalize_event(event_value),
                status=event_status,
                confidence=0.92 if event_status == "extracted" else 0.55,
                evidence=[event_reason] if event_status == "extracted" else [],
                candidates=[event_value] if event_value else ["流量"],
                reason=event_reason,
            ),
            "country": _local_field(
                value=country_candidates[0] if len(country_candidates) == 1 else None,
                normalized_value=(
                    COUNTRY_CODES.get(country_candidates[0], country_candidates[0])
                    if len(country_candidates) == 1
                    else None
                ),
                status=(
                    "extracted"
                    if len(country_candidates) == 1
                    else "conflict"
                    if len(country_candidates) > 1
                    else "missing"
                ),
                confidence=0.92 if len(country_candidates) == 1 else 0.55,
                evidence=country_candidates[:3],
                candidates=country_candidates,
                reason=(
                    "本地规则识别到唯一投放国家。"
                    if len(country_candidates) == 1
                    else "本地规则识别到多个可能国家，需要人工确认。"
                    if len(country_candidates) > 1
                    else "本地规则未识别到投放国家。"
                ),
            ),
            "age_min": _local_field(
                value=age_min,
                normalized_value=age_min,
                status="extracted" if age_min else "suggested",
                confidence=0.9 if age_min else 0.45,
                evidence=[f"{age_min}-{age_max}"] if age_min and age_max else [],
                candidates=[age_min] if age_min else ["不限"],
                reason="本地规则识别到年龄范围。" if age_min else "未写最小年龄，建议不限。",
            ),
            "age_max": _local_field(
                value=age_max,
                normalized_value=age_max,
                status="extracted" if age_max else "suggested",
                confidence=0.9 if age_max else 0.45,
                evidence=[f"{age_min}-{age_max}"] if age_min and age_max else [],
                candidates=[age_max] if age_max else ["不限"],
                reason="本地规则识别到年龄范围。" if age_max else "未写最大年龄，建议不限。",
            ),
            "gender": _local_field(
                value=gender_value or "不限",
                normalized_value=_normalize_gender(gender_value),
                status=gender_status,
                confidence=0.9 if gender_status == "extracted" else 0.45,
                evidence=[gender_value] if gender_value else [],
                candidates=[gender_value] if gender_value else ["不限"],
                reason=gender_reason,
            ),
            "audience_description_raw": _local_field(
                value=audience_raw,
                normalized_value=audience_raw,
                status="extracted" if audience_raw else "suggested",
                confidence=0.85 if audience_raw else 0.45,
                evidence=[audience_raw] if audience_raw else [],
                candidates=[audience_raw] if audience_raw else [],
                reason=(
                    "本地规则保留投放人群原文。"
                    if audience_raw
                    else "未识别到人群原文，可按不限或人工补充。"
                ),
            ),
        },
        "review": {"source": "local_rules", "llm_skipped": True},
    }
    return _normalize_delivery_extraction(data)


def _can_use_local_delivery_extraction(extraction: WorkOrderDeliveryExtractionRead) -> bool:
    fields = extraction.fields
    return (
        fields.landing_url.status == "extracted"
        and fields.country.status == "extracted"
        and fields.event_name.status in {"extracted", "suggested"}
        and fields.gender.status in {"extracted", "suggested"}
        and fields.age_min.status in {"extracted", "suggested"}
        and fields.age_max.status in {"extracted", "suggested"}
    )


def _local_field(
    *,
    value: object,
    normalized_value: object,
    status: str,
    confidence: float,
    evidence: list,
    candidates: list,
    reason: str,
) -> dict:
    return {
        "value": value,
        "normalized_value": normalized_value,
        "status": status,
        "confidence": confidence,
        "evidence": evidence,
        "candidates": [item for item in candidates if item is not None],
        "reason": reason,
    }


def _find_urls(raw_content: str) -> list[str]:
    urls = re.findall(r"https?://[^\s，,。；;）)】\]]+", raw_content, flags=re.I)
    return list(dict.fromkeys(url.strip() for url in urls))


def _find_country_candidates(raw_content: str, parsed_fields: dict) -> list[str]:
    candidates = []
    country_field = _optional_text(parsed_fields.get("country"))
    if country_field:
        candidates.extend(_countries_from_text(country_field))
    candidates.extend(_countries_from_text(raw_content))
    return list(dict.fromkeys(candidates))


def _countries_from_text(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    for alias, country in COUNTRY_ALIASES.items():
        escaped = re.escape(alias.lower())
        pattern = escaped if _has_cjk(alias) else rf"(?<![a-z]){escaped}(?![a-z])"
        if re.search(pattern, lowered):
            found.append(country)
    return found


def _find_event(raw_content: str, parsed_fields: dict) -> tuple[str, str, str]:
    source = _optional_text(parsed_fields.get("event_name")) or raw_content
    normalized = re.sub(r"\s+", "", source.lower())
    detected_event = custom_event_label(source)
    if detected_event:
        return detected_event, "extracted", "local rules recognized an external optimization event."
    if any(
        keyword in normalized
        for keyword in ["购物", "购买", "下单", "purchase", "shop", "buy"]
    ):
        return "购物", "extracted", "本地规则识别到购买/购物事件。"
    if any(keyword in normalized for keyword in ["加购", "addtocart", "add_to_cart", "cart"]):
        return "加购", "extracted", "本地规则识别到加购事件。"
    if any(keyword in normalized for keyword in ["注册", "线索", "lead", "signup"]):
        return "线索", "extracted", "本地规则识别到注册/线索事件。"
    if any(keyword in normalized for keyword in ["流量", "点击", "traffic", "click"]):
        return "流量", "extracted", "本地规则识别到流量/点击事件。"
    return "流量", "suggested", "未明确写投放事件，默认建议流量。"


def _normalize_event(event_name: str | None) -> str | None:
    external_event_type = custom_event_type(event_name)
    if external_event_type:
        return external_event_type
    event_key = custom_event_key(event_name)
    if event_key:
        return event_key
    if event_name == "购物":
        return "purchase"
    if event_name == "加购":
        return "add_to_cart"
    if event_name == "线索":
        return "lead"
    if event_name == "流量":
        return "traffic"
    return event_name


def _find_age_range(raw_content: str, parsed_fields: dict) -> tuple[int | None, int | None]:
    audience = _optional_text(parsed_fields.get("audience_description"))
    source = "\n".join(part for part in [audience, raw_content] if part)
    pattern = re.compile(r"(?:年龄|age)?[^\d]{0,8}(\d{2})\s*(?:-|~|至|到|—|–)\s*(\d{2})", re.I)
    match = pattern.search(source)
    if not match:
        return None, None
    age_min = int(match.group(1))
    age_max = int(match.group(2))
    if 13 <= age_min <= age_max <= 65:
        return age_min, age_max
    return None, None


def _find_gender(raw_content: str, parsed_fields: dict) -> tuple[str | None, str, str]:
    audience = _optional_text(parsed_fields.get("audience_description"))
    source = "\n".join(part for part in [audience, raw_content] if part)
    lowered = source.lower()
    has_female = "女" in lowered or re.search(r"\b(female|women|woman)\b", lowered) is not None
    has_male = "男" in lowered or re.search(r"\b(male|men|man)\b", lowered) is not None
    if has_male and not has_female:
        return "男", "extracted", "本地规则识别到男性定向。"
    if has_female and not has_male:
        return "女", "extracted", "本地规则识别到女性定向。"
    if any(keyword in lowered for keyword in ["不限", "all gender", "all genders"]):
        return "不限", "extracted", "本地规则识别到不限性别。"
    return None, "suggested", "未写性别，建议不限。"


def _normalize_gender(gender: str | None) -> str:
    if gender == "男":
        return "male"
    if gender == "女":
        return "female"
    return "all"


def _find_audience_raw(
    raw_content: str,
    parsed_fields: dict,
    age_min: int | None,
    age_max: int | None,
    gender: str | None,
) -> str | None:
    audience = _optional_text(parsed_fields.get("audience_description"))
    if audience:
        return audience

    for line in raw_content.replace("\r\n", "\n").split("\n"):
        if any(keyword in line for keyword in ["投放人群", "目标人群", "受众", "人群"]):
            return line.strip()

    parts = []
    if gender:
        parts.append(gender)
    if age_min and age_max:
        parts.append(f"年龄{age_min}-{age_max}")
    return "，".join(parts) if parts else None


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _delivery_extraction_cache_key(raw_content: str, provider: object) -> str:
    provider_identity = f"{provider.__class__.__name__}:{getattr(provider, 'model', '')}"
    normalized_content = raw_content.strip().replace("\r\n", "\n").replace("\r", "\n")
    digest = hashlib.sha256(
        f"{DELIVERY_EXTRACTION_CACHE_SCHEMA}\n{provider_identity}\n{normalized_content}".encode()
    ).hexdigest()
    return digest


def _get_cached_delivery_extraction(cache_key: str) -> WorkOrderDeliveryExtractionRead | None:
    entry = _DELIVERY_EXTRACTION_CACHE.get(cache_key)
    if not entry:
        return None

    cached_at, data = entry
    if monotonic() - cached_at > DELIVERY_EXTRACTION_CACHE_TTL_SECONDS:
        _DELIVERY_EXTRACTION_CACHE.pop(cache_key, None)
        return None

    _DELIVERY_EXTRACTION_CACHE.move_to_end(cache_key)
    return WorkOrderDeliveryExtractionRead.model_validate(copy.deepcopy(data))


def _set_cached_delivery_extraction(cache_key: str, data: dict) -> None:
    _DELIVERY_EXTRACTION_CACHE[cache_key] = (monotonic(), copy.deepcopy(data))
    _DELIVERY_EXTRACTION_CACHE.move_to_end(cache_key)
    while len(_DELIVERY_EXTRACTION_CACHE) > DELIVERY_EXTRACTION_CACHE_MAX_SIZE:
        _DELIVERY_EXTRACTION_CACHE.popitem(last=False)


def _normalize_delivery_field(raw_field: object, key: str) -> dict:
    field = raw_field if isinstance(raw_field, dict) else {}
    value = field.get("value")
    normalized_value = field.get("normalized_value", value)
    status = str(field.get("status") or "missing")
    if status not in DELIVERY_FIELD_STATUSES:
        status = "missing"

    return {
        "value": value,
        "normalized_value": normalized_value,
        "status": status,
        "confidence": _confidence(field.get("confidence")),
        "evidence": _text_list(field.get("evidence")),
        "candidates": _list_value(field.get("candidates")),
        "reason": _optional_text(field.get("reason")) or _default_reason(key, status),
    }


def _fields_with_status(fields: dict[str, dict], status: str) -> list[str]:
    return [key for key, value in fields.items() if value.get("status") == status]


def _reviewed_text(reviewed_fields: dict, key: str) -> str | None:
    value = reviewed_fields.get(key)
    if isinstance(value, dict):
        value = value.get("value") or value.get("normalized_value")
    return _optional_text(value)


def _build_audience_description(reviewed_fields: dict) -> str | None:
    raw = _reviewed_text(reviewed_fields, "audience_description_raw")
    parts = [raw] if raw else []

    gender = _reviewed_text(reviewed_fields, "gender")
    if gender and gender not in {"不限", "all"}:
        parts.append(gender)

    age_min = _reviewed_text(reviewed_fields, "age_min")
    age_max = _reviewed_text(reviewed_fields, "age_max")
    if age_min and age_max:
        parts.append(f"年龄{age_min}-{age_max}")
    elif age_min and age_min != "不限":
        parts.append(f"年龄{age_min}+")

    if not parts and (gender == "不限" or age_min == "不限" or age_max == "不限"):
        return "不限"
    return "。".join(dict.fromkeys(parts)) if parts else None


def _confidence(value: object) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(1, score))


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _text_list(value: object) -> list[str]:
    return [str(item) for item in _list_value(value) if str(item).strip()]


def _list_value(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _default_reason(key: str, status: str) -> str:
    if status == "missing":
        return f"{key} 未识别到，需要人工补充。"
    if status == "conflict":
        return f"{key} 识别到多个候选值，需要人工确认。"
    if status == "suggested":
        return f"{key} 为建议值，需要人工确认。"
    return f"{key} 已识别，需要人工复核。"
