from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.work_order import WorkOrder
from backend.app.integrations.llm.factory import get_llm_provider
from backend.app.schemas.work_order import (
    WorkOrderCreate,
    WorkOrderDeliveryExtractionRead,
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


class WorkOrderService:
    async def extract_delivery_fields(self, raw_content: str) -> WorkOrderDeliveryExtractionRead:
        provider = get_llm_provider()
        result = await provider.extract_delivery_fields(raw_content)
        return _normalize_delivery_extraction(result)

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
