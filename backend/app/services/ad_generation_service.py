import logging
import re
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlparse

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.base import utcnow
from backend.app.db.models.ad_generation_job import AdGenerationJob
from backend.app.db.models.agent_run import AgentRun
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.landing_page_snapshot import LandingPageSnapshot
from backend.app.db.models.review import ReviewTask
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.db.models.work_order import WorkOrder
from backend.app.db.session import AsyncSessionLocal
from backend.app.schemas.ad_generation import (
    PublishingAdGenerationAssets,
    PublishingAdGenerationJobCreate,
    PublishingAdGenerationResult,
    PublishingAdGenerationReview,
    PublishingAdGenerationReviewConfirm,
    PublishingAdGenerationReviewUpdate,
    PublishingAdSetPayload,
    PublishingCampaignPayload,
)
from backend.app.schemas.work_order import (
    CampaignFromWorkOrderRequest,
    WorkOrderCreate,
)
from backend.app.services.campaign_service import CampaignService
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

SALES_EVENTS = {
    "purchase",
    "shop",
    "shopping",
    "buy",
    "order",
    "sales",
    "\u8d2d\u4e70",
    "\u8d2d\u7269",
    "\u4e0b\u5355",
}
ADD_TO_CART_EVENTS = {"add_to_cart", "addtocart", "cart", "\u52a0\u8d2d"}
LEAD_EVENTS = {"lead", "leads", "signup", "register", "\u7ebf\u7d22", "\u6ce8\u518c"}
TRAFFIC_EVENTS = {"traffic", "click", "link_click", "\u6d41\u91cf", "\u70b9\u51fb"}
VIDEO_EVENTS = {"video", "view", "engagement", "thruplay", "\u89c6\u9891", "\u4e92\u52a8"}
APP_EVENTS = {"app", "install", "app_install", "\u5e94\u7528", "\u5b89\u88c5"}
WORKFLOW_STATUSES = {
    "fields_review",
    "topic_review",
    "copy_review",
    "image_review",
    "video_review",
    "final_review",
    "reviewing",
}
CALLBACK_HISTORY_LIMIT = 5
CALLBACK_RESPONSE_TEXT_LIMIT = 2000
CALLBACK_TIMEOUT_SECONDS = 15.0


class AdGenerationService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.work_orders = WorkOrderService()
        self.campaigns = CampaignService()

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
            metadata_json={
                **(payload.metadata_json or {}),
                "return_url": str(payload.return_url) if payload.return_url else None,
            },
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job

    async def list_jobs(
        self,
        session: AsyncSession,
        limit: int,
        offset: int,
        status: str | None = None,
    ) -> list[AdGenerationJob]:
        statement = select(AdGenerationJob).order_by(AdGenerationJob.created_at.desc())
        if status:
            statement = statement.where(AdGenerationJob.status == status)
        result = await session.execute(statement.limit(limit).offset(offset))
        return list(result.scalars().all())

    async def get_job(self, session: AsyncSession, job_id: str) -> AdGenerationJob:
        return await get_required(session, AdGenerationJob, job_id)  # type: ignore[return-value]

    async def delete_job(self, session: AsyncSession, job_id: str) -> None:
        job = await self.get_job(session, job_id)
        storage_keys = await self._delete_generated_records(session, job)
        await session.delete(job)
        await session.commit()
        _delete_local_storage_keys(storage_keys, self.settings.local_storage_root)

    async def _delete_generated_records(
        self,
        session: AsyncSession,
        job: AdGenerationJob,
    ) -> set[str]:
        campaign_id, work_order_id = _generated_record_ids(job)
        if not campaign_id:
            campaign_id = await _generated_campaign_id_from_metadata(session, job.id)
        if not campaign_id and not work_order_id:
            return set()

        storage_keys: set[str] = set()
        entity_ids: set[str] = {job.id}

        if campaign_id:
            entity_ids.add(campaign_id)
            campaign_work_order_id = await session.scalar(
                select(Campaign.work_order_id).where(Campaign.id == campaign_id)
            )
            work_order_id = work_order_id or campaign_work_order_id

            topic_ids = await _ids_for_campaign(session, ContentTopic, campaign_id)
            draft_ids = await _ids_for_campaign(session, CopyDraft, campaign_id)
            creative_rows = await session.execute(
                select(CreativeAsset.id, CreativeAsset.storage_key, CreativeAsset.url).where(
                    CreativeAsset.campaign_id == campaign_id
                )
            )
            video_rows = await session.execute(
                select(VideoAsset.id, VideoAsset.storage_key, VideoAsset.url).where(
                    VideoAsset.campaign_id == campaign_id
                )
            )

            for creative_id, storage_key, url in creative_rows.all():
                entity_ids.add(creative_id)
                _collect_storage_key(storage_keys, storage_key)
                _collect_storage_key(storage_keys, _storage_key_from_public_url(url))
            for video_id, storage_key, url in video_rows.all():
                entity_ids.add(video_id)
                _collect_storage_key(storage_keys, storage_key)
                _collect_storage_key(storage_keys, _storage_key_from_public_url(url))

            entity_ids.update(topic_ids)
            entity_ids.update(draft_ids)
            if work_order_id:
                entity_ids.add(work_order_id)

            await session.execute(
                delete(ReviewTask)
                .where(ReviewTask.campaign_id == campaign_id)
                .execution_options(synchronize_session=False)
            )
            if entity_ids:
                await session.execute(
                    delete(ReviewTask)
                    .where(ReviewTask.entity_id.in_(entity_ids))
                    .execution_options(synchronize_session=False)
                )
            await session.execute(
                delete(AgentRun)
                .where(AgentRun.campaign_id == campaign_id)
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                delete(LandingPageSnapshot)
                .where(LandingPageSnapshot.campaign_id == campaign_id)
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                delete(CreativeAsset)
                .where(CreativeAsset.campaign_id == campaign_id)
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                delete(VideoAsset)
                .where(VideoAsset.campaign_id == campaign_id)
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                delete(CopyDraft)
                .where(CopyDraft.campaign_id == campaign_id)
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                delete(ContentTopic)
                .where(ContentTopic.campaign_id == campaign_id)
                .execution_options(synchronize_session=False)
            )
            await session.execute(
                delete(Campaign)
                .where(Campaign.id == campaign_id)
                .execution_options(synchronize_session=False)
            )

        if work_order_id:
            await session.execute(
                delete(LandingPageSnapshot)
                .where(LandingPageSnapshot.work_order_id == work_order_id)
                .execution_options(synchronize_session=False)
            )
            remaining_campaign_id = await session.scalar(
                select(Campaign.id).where(Campaign.work_order_id == work_order_id).limit(1)
            )
            if not remaining_campaign_id:
                await session.execute(
                    delete(WorkOrder)
                    .where(WorkOrder.id == work_order_id)
                    .execution_options(synchronize_session=False)
                )

        return storage_keys

    def review_url_for_job(self, job_id: str) -> str:
        base_url = self.settings.ad_generation_review_base_url.rstrip("/")
        review_url = f"{base_url}/review/ad-generation/{job_id}"
        return _with_access_token(review_url, self.settings.ai_ads_access_token)

    def return_url_for_job(self, job: AdGenerationJob) -> str | None:
        metadata = job.metadata_json or {}
        return _text_or_none(metadata.get("return_url")) or _text_or_none(
            self.settings.ai_ads_return_url
        )

    def result_url_for_job(self, job_id: str) -> str:
        base_url = self.settings.public_base_url.rstrip("/")
        prefix = self.settings.api_v1_prefix.rstrip("/")
        return f"{base_url}{prefix}/integrations/publishing/ad-generation/jobs/{job_id}/result"

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
            job.status = "fields_review"
            job.metadata_json = {
                **(job.metadata_json or {}),
                "review_url": self.review_url_for_job(job.id),
                "workflow_stage": "fields_review",
            }
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

    async def update_review_payload(
        self,
        session: AsyncSession,
        job_id: str,
        payload: PublishingAdGenerationReviewUpdate,
    ) -> AdGenerationJob:
        job = await self.get_job(session, job_id)
        result_payload = _merge_result_payload(job.result_payload or {}, payload.result_payload)
        if job.status == "returned":
            next_status = "returned"
        else:
            next_status = _workflow_status_from_payload(result_payload) or "reviewing"
        result_payload["status"] = next_status
        job.result_payload = result_payload
        job.status = next_status
        job.metadata_json = {
            **(job.metadata_json or {}),
            "review_notes": payload.review_notes,
            "review_updated_at": utcnow().isoformat(),
            "workflow_stage": next_status,
        }
        await session.commit()
        await session.refresh(job)
        return job

    async def confirm_review(
        self,
        session: AsyncSession,
        job_id: str,
        payload: PublishingAdGenerationReviewConfirm,
    ) -> AdGenerationJob:
        job = await self.get_job(session, job_id)
        result_payload = job.result_payload or {}
        if payload.result_payload is not None:
            result_payload = _merge_result_payload(result_payload, payload.result_payload)
        result_payload["status"] = "returned"
        job.result_payload = result_payload
        job.status = "returned"
        job.completed_at = utcnow()
        job.metadata_json = {
            **(job.metadata_json or {}),
            "review_notes": payload.review_notes,
            "review_confirmed_at": utcnow().isoformat(),
            "workflow_stage": "returned",
        }
        await session.commit()
        await session.refresh(job)
        await self._notify_callback(session, job)
        await session.refresh(job)
        return job

    async def _notify_callback(self, session: AsyncSession, job: AdGenerationJob) -> None:
        if not job.callback_url:
            return

        payload = self._callback_payload(job)
        callback_result = await self._post_callback(job.callback_url, payload)
        job.metadata_json = _with_callback_result(job.metadata_json, callback_result)
        await session.commit()

    def _callback_payload(self, job: AdGenerationJob) -> dict[str, Any]:
        return {
            "event": "ad_generation.returned",
            "job_id": job.id,
            "external_order_id": job.external_order_id,
            "status": job.status,
            "result_url": self.result_url_for_job(job.id),
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    async def _post_callback(self, callback_url: str, payload: dict[str, Any]) -> dict[str, Any]:
        started_at = utcnow()
        result: dict[str, Any] = {
            "url": callback_url,
            "status": "pending",
            "request_payload": payload,
            "started_at": started_at.isoformat(),
        }
        try:
            async with httpx.AsyncClient(timeout=CALLBACK_TIMEOUT_SECONDS) as client:
                response = await client.post(callback_url, json=payload)
        except httpx.HTTPError as exc:
            return {
                **result,
                "status": "failed",
                "error": str(exc),
                "finished_at": utcnow().isoformat(),
            }

        response_text = _trim_response_text(response.text)
        if 200 <= response.status_code < 300:
            return {
                **result,
                "status": "succeeded",
                "status_code": response.status_code,
                "response_text": response_text,
                "finished_at": utcnow().isoformat(),
            }

        return {
            **result,
            "status": "failed",
            "status_code": response.status_code,
            "response_text": response_text,
            "error": f"Callback endpoint returned HTTP {response.status_code}.",
            "finished_at": utcnow().isoformat(),
        }

    async def _generate_result(
        self,
        session: AsyncSession,
        job: AdGenerationJob,
        payload: PublishingAdGenerationJobCreate,
    ) -> PublishingAdGenerationResult:
        raw_content = payload.work_order.raw_content
        structured_fields = payload.work_order.structured_fields or {}
        if payload.work_order.delivery_extraction:
            extraction_data = payload.work_order.delivery_extraction.model_dump(mode="json")
            extraction_source = "provided"
        else:
            extraction = await self.work_orders.extract_delivery_fields(raw_content)
            extraction_data = extraction.model_dump(mode="json")
            extraction_source = "llm"
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
                    "extraction_source": extraction_source,
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

        campaign.metadata_json = {
            **(campaign.metadata_json or {}),
            "work_order": {
                "raw_content": raw_content,
                "parsed_fields": work_order.parsed_fields,
                "country": work_order.country,
                "media": work_order.media,
                "landing_url": landing_url,
                "report_timezone": work_order.report_timezone,
            },
        }
        if landing_url:
            campaign.metadata_json = {
                **(campaign.metadata_json or {}),
                "landing_page": {"url": landing_url, "status": "provided"},
            }
        await session.commit()
        await session.refresh(campaign)

        warnings: list[str] = []
        if payload.preferences.creative_type != "image":
            warnings.append(
                f"{payload.preferences.creative_type} creative generation is reserved; "
                "workflow starts with targeting and requires manual creative production."
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
        gender = _text_or_none(_field_value(reviewed_fields, "gender"))
        audience_description = _text_or_none(
            _field_value(reviewed_fields, "audience_description_raw")
        )

        if _has_multiple_country_values(structured_fields):
            warnings.append(
                "Multiple country values were received; only the first country is returned "
                "because the publishing system ad set is single-select."
            )

        missing_fields = _missing_fields(
            landing_url=landing_url,
            country_code=None if country_is_missing else country_code,
        )
        review = PublishingAdGenerationReview(
            missing_fields=missing_fields,
            warnings=warnings,
            low_confidence_fields=_low_confidence_fields(llm_fields, structured_fields),
        )

        result = PublishingAdGenerationResult(
            job_id=job.id,
            external_order_id=payload.external_order_id,
            status="fields_review",
            campaign_payload=PublishingCampaignPayload(
                name=_trim(campaign.name, 255),
                objective=_campaign_objective(event_name),
            ),
            adset_payload=PublishingAdSetPayload(
                name=_adset_name(country_code, event_name, age_min, age_max),
                daily_budget=payload.preferences.daily_budget,
                optimization_goal=_optimization_goal(event_name),
                event_name=_text_or_none(event_name),
                countries=country_code,
                country_code=country_code,
                country_label=country_label,
                age_min=age_min,
                age_max=age_max,
                gender=gender,
                audience_description=audience_description,
            ),
            creative_payload=None,
            assets=PublishingAdGenerationAssets(),
            review=review,
            metadata_json={
                "source": "publishing_system",
                "workflow_stage": "fields_review",
                "work_order_id": work_order.id,
                "campaign_id": campaign.id,
                "reviewed_delivery_fields": reviewed_fields,
                "llm_extraction_review": extraction_data.get("review") or {},
                "extraction_source": extraction_source,
            },
        )
        return result


async def _ids_for_campaign(
    session: AsyncSession,
    model: type[ContentTopic] | type[CopyDraft],
    campaign_id: str,
) -> list[str]:
    result = await session.execute(select(model.id).where(model.campaign_id == campaign_id))
    return list(result.scalars().all())


def _generated_record_ids(job: AdGenerationJob) -> tuple[str | None, str | None]:
    result_payload = job.result_payload or {}
    metadata = result_payload.get("metadata_json")
    if not isinstance(metadata, dict):
        metadata = {}

    campaign_id = _text_or_none(metadata.get("campaign_id"))
    work_order_id = _text_or_none(metadata.get("work_order_id"))
    return campaign_id, work_order_id


async def _generated_campaign_id_from_metadata(
    session: AsyncSession,
    job_id: str,
) -> str | None:
    result = await session.execute(select(Campaign.id, Campaign.metadata_json))
    for campaign_id, metadata in result.all():
        if isinstance(metadata, dict) and metadata.get("ad_generation_job_id") == job_id:
            return campaign_id
    return None


def _collect_storage_key(storage_keys: set[str], storage_key: str | None) -> None:
    if storage_key and storage_key.startswith("local://"):
        storage_keys.add(storage_key)


def _storage_key_from_public_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.startswith("/storage/"):
        return None
    storage_path = unquote(parsed.path.removeprefix("/storage/").lstrip("/"))
    return f"local://{storage_path}" if storage_path else None


def _delete_local_storage_keys(storage_keys: set[str], storage_root: str) -> None:
    root = Path(storage_root).resolve()
    for storage_key in storage_keys:
        target_path = _local_path_from_storage_key(storage_key, root)
        if not target_path:
            continue
        try:
            if target_path.is_file():
                target_path.unlink()
                _remove_empty_storage_dirs(target_path.parent, root)
        except OSError as exc:
            logger.warning("Failed to delete local asset %s: %s", storage_key, exc)


def _local_path_from_storage_key(storage_key: str, root: Path) -> Path | None:
    raw_path = storage_key.removeprefix("local://").lstrip("/\\")
    relative_path = PurePosixPath(raw_path)
    if not raw_path or relative_path.is_absolute() or ".." in relative_path.parts:
        return None

    target_path = (root / Path(*relative_path.parts)).resolve()
    try:
        target_path.relative_to(root)
    except ValueError:
        return None
    return target_path


def _remove_empty_storage_dirs(path: Path, root: Path) -> None:
    current = path
    while current != root:
        try:
            current.relative_to(root)
            current.rmdir()
        except OSError:
            break
        except ValueError:
            break
        current = current.parent


def _merge_result_payload(current: dict, updates: dict) -> dict:
    merged = dict(current)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def _with_access_token(url: str, access_token: str | None) -> str:
    if not access_token:
        return url
    parsed = urlparse(url)
    query_items = [(key, value) for key, value in parse_qsl(parsed.query) if key != "access_token"]
    query_items.append(("access_token", access_token))
    return parsed._replace(query=urlencode(query_items)).geturl()


def _workflow_status_from_payload(payload: dict) -> str | None:
    metadata = payload.get("metadata_json")
    if isinstance(metadata, dict):
        stage = metadata.get("workflow_stage")
        if isinstance(stage, str) and stage in WORKFLOW_STATUSES:
            return stage
    status = payload.get("status")
    if isinstance(status, str) and status in WORKFLOW_STATUSES:
        return status
    return None


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


def _optimization_goal(event_name: Any) -> str:
    event_key = _event_key(event_name)
    if event_key in SALES_EVENTS or event_key in ADD_TO_CART_EVENTS or event_key in LEAD_EVENTS:
        return "OFFSITE_CONVERSIONS"
    if event_key in VIDEO_EVENTS:
        return "THRUPLAY"
    if event_key in APP_EVENTS:
        return "APP_INSTALLS"
    return "LINK_CLICKS"


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


def _adset_name(country_code: str, event_name: Any, age_min: int, age_max: int) -> str:
    event_label = _text_or_none(event_name) or "traffic"
    return _trim(f"{country_code} - {event_label} - {age_min}-{age_max}", 255)


def _missing_fields(landing_url: Any, country_code: str | None) -> list[str]:
    missing = []
    if not _text_or_none(landing_url):
        missing.append("landing_url")
    if not _text_or_none(country_code):
        missing.append("country")
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


def _with_callback_result(metadata: dict | None, callback_result: dict[str, Any]) -> dict:
    current = dict(metadata or {})
    previous = current.get("callback_delivery")
    previous = previous if isinstance(previous, dict) else {}
    try:
        attempts = int(previous.get("attempts") or 0) + 1
    except (TypeError, ValueError):
        attempts = 1

    last_result = {**callback_result, "attempt": attempts}
    history = previous.get("history") if isinstance(previous.get("history"), list) else []
    history = [item for item in history if isinstance(item, dict)]
    history = [*history, last_result][-CALLBACK_HISTORY_LIMIT:]

    return {
        **current,
        "callback_delivery": {
            "status": callback_result.get("status"),
            "attempts": attempts,
            "last_status_code": callback_result.get("status_code"),
            "last_attempted_at": callback_result.get("started_at"),
            "last_result": last_result,
            "history": history,
        },
    }


def _trim_response_text(value: str) -> str:
    if len(value) <= CALLBACK_RESPONSE_TEXT_LIMIT:
        return value
    return value[: CALLBACK_RESPONSE_TEXT_LIMIT - 3].rstrip() + "..."


def _trim(value: str, length: int) -> str:
    return value[:length].strip()


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
