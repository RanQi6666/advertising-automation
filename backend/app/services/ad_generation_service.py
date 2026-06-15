import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.base import utcnow
from backend.app.db.models.ad_generation_job import AdGenerationJob
from backend.app.db.session import AsyncSessionLocal
from backend.app.schemas.ad_generation import (
    PublishingAdGenerationAssets,
    PublishingAdGenerationJobCreate,
    PublishingAdGenerationResult,
    PublishingAdGenerationReview,
    PublishingAdSetPayload,
    PublishingCampaignPayload,
    PublishingCreativePayload,
    PublishingGeneratedImage,
)
from backend.app.schemas.copywriting import CopyGenerateRequest
from backend.app.schemas.creative import CreativeGenerateRequest
from backend.app.schemas.topic import TopicGenerateRequest
from backend.app.schemas.work_order import CampaignFromWorkOrderRequest, WorkOrderCreate
from backend.app.services.campaign_service import CampaignService
from backend.app.services.copywriting_service import CopywritingService
from backend.app.services.creative_asset_urls import resolve_creative_image_url
from backend.app.services.creative_service import CreativeService
from backend.app.services.topic_service import TopicService
from backend.app.services.utils import get_required
from backend.app.services.work_order_service import WorkOrderService

logger = logging.getLogger(__name__)

DELIVERY_FIELD_ALIASES = {
    "landing_url": ("landing_url", "link", "destination_url", "url"),
    "event_name": ("event_name", "event", "conversion_event", "custom_event_type", "objective"),
    "country": ("country", "country_code", "countries"),
    "age_min": ("age_min", "min_age"),
    "age_max": ("age_max", "max_age"),
    "gender": ("gender", "sex"),
    "audience_description_raw": ("audience_description_raw", "audience", "target_audience"),
}

COUNTRY_LABELS_BY_CODE = {
    "BR": "Brazil",
    "ID": "Indonesia",
    "IN": "India",
    "MX": "Mexico",
    "MY": "Malaysia",
    "PH": "Philippines",
    "SG": "Singapore",
    "TH": "Thailand",
    "US": "United States",
    "VN": "Vietnam",
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
    "\u5370\u5ea6": "IN",
    "\u5370\u5c3c": "ID",
    "\u5370\u5ea6\u5c3c\u897f\u4e9a": "ID",
    "\u5df4\u897f": "BR",
    "\u65b0\u52a0\u5761": "SG",
    "\u6cf0\u56fd": "TH",
    "\u7f8e\u56fd": "US",
    "\u83f2\u5f8b\u5bbe": "PH",
    "\u58a8\u897f\u54e5": "MX",
    "\u8d8a\u5357": "VN",
    "\u9a6c\u6765\u897f\u4e9a": "MY",
}

SALES_EVENTS = {"purchase", "shop", "buy", "order", "sales", "\u8d2d\u4e70", "\u4e0b\u5355"}
ADD_TO_CART_EVENTS = {"add_to_cart", "addtocart", "cart", "\u52a0\u8d2d"}
LEAD_EVENTS = {"lead", "leads", "signup", "register", "\u7ebf\u7d22", "\u6ce8\u518c"}
TRAFFIC_EVENTS = {"traffic", "click", "link_click", "\u6d41\u91cf", "\u70b9\u51fb"}
VIDEO_EVENTS = {"video", "view", "engagement", "thruplay", "\u89c6\u9891", "\u4e92\u52a8"}
APP_EVENTS = {"app", "install", "app_install", "\u5e94\u7528", "\u5b89\u88c5"}


class AdGenerationService:
    def __init__(self) -> None:
        self.work_orders = WorkOrderService()
        self.campaigns = CampaignService()
        self.topics = TopicService()
        self.copywriting = CopywritingService()
        self.creatives = CreativeService()

    async def create_job(
        self,
        session: AsyncSession,
        payload: PublishingAdGenerationJobCreate,
    ) -> AdGenerationJob:
        job = AdGenerationJob(
            external_order_id=payload.external_order_id,
            status="queued",
            callback_url=str(payload.callback_url) if payload.callback_url else None,
            request_payload=payload.model_dump(mode="json"),
            result_payload={},
            metadata_json=payload.metadata_json or {},
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job

    async def get_job(self, session: AsyncSession, job_id: str) -> AdGenerationJob:
        return await get_required(session, AdGenerationJob, job_id)  # type: ignore[return-value]

    async def run_job(self, job_id: str) -> None:
        async with AsyncSessionLocal() as session:
            await self.process_job(session, job_id)

    async def process_job(self, session: AsyncSession, job_id: str) -> AdGenerationJob:
        job = await self.get_job(session, job_id)
        job.status = "processing"
        job.started_at = utcnow()
        job.completed_at = None
        job.error_message = None
        await session.commit()
        await session.refresh(job)

        try:
            payload = PublishingAdGenerationJobCreate.model_validate(job.request_payload)
            result = await self._generate_result(session, job, payload)
            job.result_payload = result.model_dump(mode="json")
            job.status = "completed"
            job.completed_at = utcnow()
            await session.commit()
            await session.refresh(job)
            return job
        except Exception as exc:
            await session.rollback()
            logger.exception("Ad generation job failed: %s", job_id)
            failed_job = await self.get_job(session, job_id)
            failed_job.status = "failed"
            failed_job.error_message = str(exc)
            failed_job.completed_at = utcnow()
            await session.commit()
            await session.refresh(failed_job)
            return failed_job

    async def _generate_result(
        self,
        session: AsyncSession,
        job: AdGenerationJob,
        payload: PublishingAdGenerationJobCreate,
    ) -> PublishingAdGenerationResult:
        raw_content = payload.work_order.raw_content
        structured_fields = payload.work_order.structured_fields or {}
        extraction = await self.work_orders.extract_delivery_fields(raw_content)
        extraction_data = extraction.model_dump(mode="json")
        llm_fields = extraction_data.get("fields") or {}
        reviewed_fields = _build_reviewed_fields(structured_fields, llm_fields)

        work_order = await self.work_orders.create_work_order(
            session,
            WorkOrderCreate(
                raw_content=raw_content,
                reviewed_delivery_fields=reviewed_fields,
                llm_delivery_fields=llm_fields,
                metadata_json={
                    "source": "publishing_system",
                    "external_order_id": payload.external_order_id,
                    "structured_fields": structured_fields,
                    "extraction_review": extraction_data.get("review") or {},
                },
            ),
        )

        landing_url = _field_value(reviewed_fields, "landing_url") or work_order.landing_url
        event_name = _field_value(reviewed_fields, "event_name") or work_order.event_name
        campaign = await self.campaigns.create_campaign_from_work_order(
            session,
            work_order.id,
            CampaignFromWorkOrderRequest(
                name=_campaign_name(structured_fields, work_order.project_name),
                objective=str(event_name) if event_name else None,
                product_name=_text_or_none(_structured_value(structured_fields, "product_name")),
                audience_description=_text_or_none(
                    _field_value(reviewed_fields, "audience_description_raw")
                ),
                metadata_json={"source": "publishing_system", "ad_generation_job_id": job.id},
            ),
        )

        if landing_url:
            campaign.metadata_json = {
                **(campaign.metadata_json or {}),
                "landing_page": {"url": landing_url, "status": "provided"},
            }
            await session.commit()
            await session.refresh(campaign)

        topic = (
            await self.topics.generate_topics(
                session,
                TopicGenerateRequest(
                    campaign_id=campaign.id,
                    limit=1,
                    signals={"integration": "publishing_system"},
                ),
            )
        )[0]
        draft = await self.copywriting.generate_copy(
            session,
            CopyGenerateRequest(
                topic_id=topic.id,
                constraints={"channel": "facebook_ad", "format": "publishing_system"},
            ),
        )

        warnings: list[str] = []
        creative_assets = []
        if payload.preferences.creative_type == "image":
            creative_assets = await self.creatives.generate_creatives(
                session,
                CreativeGenerateRequest(
                    draft_id=draft.id,
                    count=payload.preferences.image_count,
                    size="1:1",
                ),
            )
        else:
            warnings.append(
                f"{payload.preferences.creative_type} creative generation is reserved; "
                "returning copy and targeting only."
            )

        images = [
            PublishingGeneratedImage(
                id=asset.id,
                filename=_filename_from_url(asset.url),
                url=resolve_creative_image_url(asset, self.creatives.image_storage),
                size=asset.size,
                prompt=asset.prompt,
                alt_text=asset.alt_text,
            )
            for asset in creative_assets
        ]
        if creative_assets and not any(image.url for image in images):
            warnings.append(
                "Image provider did not return a public asset URL; publishing system needs a "
                "material-library upload or a real image provider."
            )

        country_value = _field_value(reviewed_fields, "country")
        country_is_missing = not _text_or_none(country_value)
        country_code, country_label = _country_code_and_label(country_value)
        if country_is_missing:
            warnings.append(
                "Country was not recognized; defaulted to US and requires operator confirmation."
            )
        age_min = _age_value(_field_value(reviewed_fields, "age_min"), default=18)
        age_max = _age_value(_field_value(reviewed_fields, "age_max"), default=65)
        if age_min > age_max:
            age_min, age_max = age_max, age_min

        pixel_id = _text_or_none(
            _structured_value(
                structured_fields,
                "pixel_id",
                aliases=("pixel", "facebook_pixel_id", "meta_pixel_id"),
            )
        )
        custom_event_type = _custom_event_type(event_name)
        optimization_goal = (
            "OFFSITE_CONVERSIONS" if pixel_id and custom_event_type else "LINK_CLICKS"
        )
        if custom_event_type and not pixel_id:
            warnings.append(
                "Conversion event was recognized, but no pixel_id was provided; "
                "ad set falls back to LINK_CLICKS."
            )

        if _has_multiple_country_values(structured_fields):
            warnings.append(
                "Multiple country values were received; only the first country is returned "
                "because the publishing system ad set is single-select."
            )

        missing_fields = _missing_fields(
            landing_url=landing_url,
            country_code=None if country_is_missing else country_code,
            message=draft.primary_text or draft.body,
        )
        review = PublishingAdGenerationReview(
            missing_fields=missing_fields,
            warnings=warnings,
            low_confidence_fields=_low_confidence_fields(llm_fields, structured_fields),
        )

        headline = _trim(draft.headline or topic.title, 255)
        creative_url = next((image.url for image in images if image.url), None)
        result = PublishingAdGenerationResult(
            job_id=job.id,
            external_order_id=payload.external_order_id,
            status="completed",
            campaign_payload=PublishingCampaignPayload(
                name=_trim(campaign.name, 255),
                objective=_campaign_objective(event_name),
            ),
            adset_payload=PublishingAdSetPayload(
                name=_adset_name(country_code, optimization_goal, age_min, age_max),
                daily_budget=payload.preferences.daily_budget,
                billing_event="IMPRESSIONS",
                optimization_goal=optimization_goal,
                bid_strategy="LOWEST_COST_WITHOUT_CAP",
                pixel_id=pixel_id,
                custom_event_type=custom_event_type if pixel_id else None,
                countries=country_code,
                country_code=country_code,
                country_label=country_label,
                age_min=age_min,
                age_max=age_max,
            ),
            creative_payload=PublishingCreativePayload(
                name=_trim(f"{campaign.name} - {payload.preferences.creative_type}", 255),
                type=payload.preferences.creative_type,
                message=draft.primary_text or draft.body,
                link=_text_or_none(landing_url),
                ads_name=headline,
                description=draft.description,
                asset_url=creative_url,
                asset_id=None,
            ),
            assets=PublishingAdGenerationAssets(images=images),
            review=review,
            metadata_json={
                "source": "publishing_system",
                "work_order_id": work_order.id,
                "campaign_id": campaign.id,
                "topic_id": topic.id,
                "draft_id": draft.id,
                "creative_asset_ids": [asset.id for asset in creative_assets],
                "llm_extraction_review": extraction_data.get("review") or {},
            },
        )
        return result


def _build_reviewed_fields(structured_fields: dict, llm_fields: dict) -> dict:
    reviewed: dict[str, Any] = {}
    for field_name, aliases in DELIVERY_FIELD_ALIASES.items():
        value = _structured_value(structured_fields, field_name, aliases=aliases)
        if value is not None:
            reviewed[field_name] = {
                "value": value,
                "normalized_value": _normalize_structured_value(field_name, value),
                "status": "extracted",
                "confidence": 1,
                "evidence": [f"structured_fields.{field_name}"],
                "candidates": [value],
                "reason": "Provided by publishing system structured fields.",
            }
            continue

        llm_field = llm_fields.get(field_name) if isinstance(llm_fields, dict) else None
        if isinstance(llm_field, dict) and (
            llm_field.get("normalized_value") is not None or llm_field.get("value") is not None
        ):
            reviewed[field_name] = llm_field
    return reviewed


def _structured_value(
    fields: dict,
    name: str,
    aliases: tuple[str, ...] | None = None,
) -> Any | None:
    keys = (name,) + tuple(key for key in (aliases or ()) if key != name)
    for key in keys:
        if key in fields and fields[key] not in (None, ""):
            return _first_value(fields[key])
    return None


def _first_value(value: Any) -> Any | None:
    if isinstance(value, (list, tuple)):
        for item in value:
            if item not in (None, ""):
                return item
        return None
    if isinstance(value, dict):
        for key in ("normalized_value", "value", "code", "id", "label", "name"):
            if value.get(key) not in (None, ""):
                return value[key]
        return None
    return value


def _normalize_structured_value(field_name: str, value: Any) -> Any:
    if field_name == "country":
        code, _ = _country_code_and_label(value)
        return code
    if field_name in {"age_min", "age_max"}:
        return _age_value(value, default=18 if field_name == "age_min" else 65)
    if field_name == "event_name":
        return _event_key(value) or value
    return value


def _field_value(fields: dict, field_name: str) -> Any | None:
    value = fields.get(field_name)
    if isinstance(value, dict):
        return value.get("normalized_value") if value.get("normalized_value") is not None else (
            value.get("value")
        )
    return value


def _country_code_and_label(value: Any) -> tuple[str, str]:
    raw = _text_or_none(_first_value(value))
    if not raw:
        return "US", COUNTRY_LABELS_BY_CODE["US"]

    normalized = raw.strip()
    if re.fullmatch(r"[A-Za-z]{2}", normalized):
        code = normalized.upper()
        return code, COUNTRY_LABELS_BY_CODE.get(code, code)

    alias_key = re.sub(r"\s+", " ", normalized.lower())
    code = COUNTRY_ALIASES.get(alias_key)
    if code:
        return code, COUNTRY_LABELS_BY_CODE.get(code, normalized)

    return normalized.upper(), normalized


def _campaign_objective(event_name: Any) -> str:
    event_key = _event_key(event_name)
    if event_key in SALES_EVENTS or event_key in ADD_TO_CART_EVENTS:
        return "OUTCOME_SALES"
    if event_key in LEAD_EVENTS:
        return "OUTCOME_LEADS"
    if event_key in VIDEO_EVENTS:
        return "OUTCOME_ENGAGEMENT"
    if event_key in APP_EVENTS:
        return "OUTCOME_APP_PROMOTION"
    return "OUTCOME_TRAFFIC"


def _custom_event_type(event_name: Any) -> str | None:
    event_key = _event_key(event_name)
    if event_key in SALES_EVENTS:
        return "PURCHASE"
    if event_key in ADD_TO_CART_EVENTS:
        return "ADD_TO_CART"
    if event_key in LEAD_EVENTS:
        return "LEAD"
    return None


def _event_key(event_name: Any) -> str | None:
    text = _text_or_none(event_name)
    if not text:
        return None
    return re.sub(r"[\s-]+", "_", text.strip().lower())


def _age_value(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    match = re.search(r"\d{1,3}", str(value))
    if not match:
        return default
    return max(13, min(65, int(match.group(0))))


def _campaign_name(fields: dict, project_name: str | None) -> str:
    structured_name = _structured_value(
        fields,
        "campaign_name",
        aliases=("campaign_name", "project_name", "product_name", "name"),
    )
    return _trim(_text_or_none(structured_name) or project_name or "AI generated campaign", 255)


def _adset_name(country_code: str, optimization_goal: str, age_min: int, age_max: int) -> str:
    return _trim(f"{country_code} - {optimization_goal} - {age_min}-{age_max}", 255)


def _missing_fields(landing_url: Any, country_code: str | None, message: str | None) -> list[str]:
    missing = []
    if not _text_or_none(landing_url):
        missing.append("landing_url")
    if not _text_or_none(country_code):
        missing.append("country")
    if not _text_or_none(message):
        missing.append("message")
    return missing


def _low_confidence_fields(llm_fields: dict, structured_fields: dict) -> list[str]:
    low_confidence = []
    for field_name, field in llm_fields.items():
        if _structured_value(structured_fields, field_name, DELIVERY_FIELD_ALIASES.get(field_name)):
            continue
        if isinstance(field, dict) and float(field.get("confidence") or 0) < 0.7:
            low_confidence.append(field_name)
    return low_confidence


def _has_multiple_country_values(fields: dict) -> bool:
    value = _structured_value(fields, "country", aliases=("country", "country_code", "countries"))
    if isinstance(fields.get("countries"), list) and len(fields["countries"]) > 1:
        return True
    if isinstance(value, str) and re.search(r"[,;/|]", value):
        return True
    return False


def _filename_from_url(url: str | None) -> str | None:
    if not url:
        return None
    return url.rstrip("/").rsplit("/", 1)[-1] or None


def _trim(value: str, length: int) -> str:
    return value[:length].strip()


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
