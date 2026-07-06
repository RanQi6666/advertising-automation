import asyncio
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppError, NotFoundError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.models.topic import ContentTopic
from backend.app.integrations.llm import get_llm_provider
from backend.app.schemas.ai import CopyDraftCandidate, TopicCandidate, VideoStoryboardCandidate
from backend.app.schemas.external_ai_generation import (
    ExternalAICopyGenerationCreate,
    ExternalAITopicSelectionCreate,
    ExternalAIVideoStoryboardCreate,
    ExternalAIWorkOrderAnalysisCreate,
)
from backend.app.services.ad_generation_service import (
    _adset_name,
    _build_reviewed_fields,
    _campaign_objective,
    _country_code_and_label,
    _field_value,
    _optimization_goal,
)
from backend.app.services.creative_strategy_builder import build_creative_strategy
from backend.app.services.custom_event_types import custom_event_type
from backend.app.services.generation_task_service import (
    TEXT_QUEUE_NAME,
    GenerationTaskService,
)
from backend.app.services.llm_rate_limit import ExternalAIIdempotencyLock, llm_text_rate_limiter
from backend.app.services.work_order_parser import parse_work_order_text
from backend.app.services.work_order_service import WorkOrderService

EXTERNAL_AI_GENERATION_SOURCE = "external_ai_generation"
EXTERNAL_AI_BUSINESS_TYPE = "external_ai"
EXTERNAL_WORK_ORDER_ANALYSIS_TASK_TYPE = "external_work_order_analysis"
EXTERNAL_TOPIC_SELECTION_TASK_TYPE = "external_topic_selection"
EXTERNAL_COPY_GENERATION_TASK_TYPE = "external_copy_generation"
EXTERNAL_VIDEO_STORYBOARD_TASK_TYPE = "external_video_storyboard"
EXTERNAL_AI_TEXT_TASK_TYPES = {
    EXTERNAL_WORK_ORDER_ANALYSIS_TASK_TYPE,
    EXTERNAL_TOPIC_SELECTION_TASK_TYPE,
    EXTERNAL_COPY_GENERATION_TASK_TYPE,
    EXTERNAL_VIDEO_STORYBOARD_TASK_TYPE,
}


class ExternalAIGenerationService:
    def __init__(self) -> None:
        self.task_service = GenerationTaskService()
        self.work_orders = WorkOrderService()

    async def create_work_order_analysis_job(
        self,
        session: AsyncSession,
        payload: ExternalAIWorkOrderAnalysisCreate,
    ) -> GenerationTask:
        return await self._create_job(
            session,
            task_type=EXTERNAL_WORK_ORDER_ANALYSIS_TASK_TYPE,
            payload=payload.model_dump(mode="json"),
            external_request_id=payload.external_request_id,
        )

    async def create_topic_selection_job(
        self,
        session: AsyncSession,
        payload: ExternalAITopicSelectionCreate,
    ) -> GenerationTask:
        return await self._create_job(
            session,
            task_type=EXTERNAL_TOPIC_SELECTION_TASK_TYPE,
            payload=payload.model_dump(mode="json"),
            external_request_id=payload.external_request_id,
        )

    async def create_copy_generation_job(
        self,
        session: AsyncSession,
        payload: ExternalAICopyGenerationCreate,
    ) -> GenerationTask:
        return await self._create_job(
            session,
            task_type=EXTERNAL_COPY_GENERATION_TASK_TYPE,
            payload=payload.model_dump(mode="json"),
            external_request_id=payload.external_request_id,
        )

    async def create_video_storyboard_job(
        self,
        session: AsyncSession,
        payload: ExternalAIVideoStoryboardCreate,
    ) -> GenerationTask:
        return await self._create_job(
            session,
            task_type=EXTERNAL_VIDEO_STORYBOARD_TASK_TYPE,
            payload=payload.model_dump(mode="json"),
            external_request_id=payload.external_request_id,
        )

    async def get_job(self, session: AsyncSession, job_id: str) -> GenerationTask:
        task = await session.get(GenerationTask, job_id)
        if task is None or task.task_type not in EXTERNAL_AI_TEXT_TASK_TYPES:
            raise NotFoundError("ai generation job not found")
        return task

    async def execute_task(self, session: AsyncSession, task: GenerationTask) -> dict[str, Any]:
        if task.task_type == EXTERNAL_WORK_ORDER_ANALYSIS_TASK_TYPE:
            return await self.execute_work_order_analysis(session, task)
        if task.task_type == EXTERNAL_TOPIC_SELECTION_TASK_TYPE:
            return await self.execute_topic_selection(session, task)
        if task.task_type == EXTERNAL_COPY_GENERATION_TASK_TYPE:
            return await self.execute_copy_generation(session, task)
        if task.task_type == EXTERNAL_VIDEO_STORYBOARD_TASK_TYPE:
            return await self.execute_video_storyboard(session, task)
        raise AppError(f"Unsupported external ai task type: {task.task_type}")

    async def execute_work_order_analysis(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> dict[str, Any]:
        del session
        payload = ExternalAIWorkOrderAnalysisCreate.model_validate(task.payload_json or {})
        structured_fields = parse_work_order_text(payload.work_order_text)
        structured_fields.setdefault("media", payload.media)
        structured_fields.setdefault("work_order_type", payload.work_order_type)

        async with llm_text_rate_limiter():
            extraction = await self.work_orders.extract_delivery_fields(payload.work_order_text)
        extraction_data = extraction.model_dump(mode="json")
        llm_fields = _dict_or_empty(extraction_data.get("fields"))
        reviewed_fields = _build_reviewed_fields(structured_fields, llm_fields)

        landing_url = _first_text(
            _field_value(reviewed_fields, "landing_url"),
            structured_fields.get("landing_url"),
        )
        event_name = _first_text(
            _field_value(reviewed_fields, "event_name"),
            structured_fields.get("event_name"),
        )
        country_value = _first_text(
            _field_value(reviewed_fields, "country"),
            structured_fields.get("country"),
        )
        country_is_missing = not country_value
        country_code, country_label = _country_code_and_label(country_value)
        age_min = _age_value(_field_value(reviewed_fields, "age_min"), default=18)
        age_max = _age_value(_field_value(reviewed_fields, "age_max"), default=65)
        if age_min > age_max:
            age_min, age_max = age_max, age_min
        gender = _first_text(
            _field_value(reviewed_fields, "gender"),
            structured_fields.get("gender"),
        )
        audience = _first_text(
            _field_value(reviewed_fields, "audience_description_raw"),
            structured_fields.get("audience_description"),
        )
        project_name = _trim(
            _first_text(
                structured_fields.get("project_name"),
                structured_fields.get("product_name"),
                "AI generated campaign",
            ),
            255,
        )
        event_type = custom_event_type(event_name)
        creative_strategy = build_creative_strategy(
            {
                "raw_content": payload.work_order_text,
                "work_order_type": payload.work_order_type,
                "structured_fields": structured_fields,
                "reviewed_fields": reviewed_fields,
                "product_name": structured_fields.get("product_name"),
                "project_name": project_name,
                "landing_url": landing_url,
                "event_name": event_name,
                "country": country_value,
                "audience_description": audience,
                "media": payload.media,
            }
        )

        warnings: list[str] = []
        if payload.media.lower() != "fb":
            warnings.append("media is not fb; returned payload still uses Facebook field names.")
        if country_is_missing:
            warnings.append("Country was not recognized; defaulted to US.")

        return {
            "request_id": _request_id(payload.external_request_id, task.id),
            "campaign_payload": {
                "name": project_name,
                "objective": _campaign_objective(event_name),
                "status": "PAUSED",
            },
            "adset_payload": {
                "name": _adset_name(country_code, event_name, age_min, age_max),
                "daily_budget": None,
                "optimization_goal": _optimization_goal(event_name),
                "event_name": event_name,
                "customEventType": event_type,
                "custom_event_type": event_type,
                "countries": country_code,
                "country_code": country_code,
                "country_label": country_label,
                "age_min": age_min,
                "age_max": age_max,
                "gender": gender,
                "audience_description": audience,
            },
            "creative_payload": {
                "name": f"{project_name}-Creative",
                "type": "image",
                "btn_type": _default_btn_type(event_type),
                "link": landing_url,
            },
            "review": {
                "missing_fields": _missing_fields(
                    landing_url,
                    None if country_is_missing else country_code,
                ),
                "warnings": warnings,
                "low_confidence_fields": _low_confidence_fields(llm_fields, structured_fields),
            },
            "assets": {"images": [], "videos": []},
            "metadata_json": {"creative_strategy": creative_strategy},
        }

    async def execute_topic_selection(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> dict[str, Any]:
        del session
        payload = ExternalAITopicSelectionCreate.model_validate(task.payload_json or {})
        campaign = _campaign_from_external_context(
            product_name=payload.product_name,
            campaign=payload.campaign,
            adset=payload.adset,
            creative=payload.creative,
            brief=payload.brief,
            country=payload.country,
            event_name=None,
            custom_event_type=None,
            audience=None,
            landing_url=None,
            language=payload.language,
            work_order_type=payload.work_order_type or payload.industry,
        )
        signals = _topic_signals(payload)
        async with llm_text_rate_limiter():
            candidates = await get_llm_provider().generate_topics(
                campaign=campaign,
                limit=payload.count,
                signals=signals,
            )
        return {
            "request_id": _request_id(payload.external_request_id, task.id),
            "topics": [_topic_candidate_payload(candidate) for candidate in candidates],
        }

    async def execute_copy_generation(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> dict[str, Any]:
        del session
        payload = ExternalAICopyGenerationCreate.model_validate(task.payload_json or {})
        campaign = _campaign_from_external_context(
            product_name=payload.product_name,
            campaign=payload.campaign,
            adset=payload.adset,
            creative=payload.creative,
            brief=payload.brief,
            country=payload.country,
            event_name=payload.event_name,
            custom_event_type=payload.customEventType,
            audience=payload.audience,
            landing_url=payload.landing_url,
            language=payload.language,
            work_order_type=None,
        )
        topic = _topic_from_copy_payload(campaign.id, payload)
        llm = get_llm_provider()
        copywritings: list[dict[str, Any]] = []
        for index in range(1, payload.count + 1):
            constraints = {
                **payload.constraints,
                "variant_index": index,
                "cta": _copy_cta(payload),
                "selling_points": payload.selling_points,
                "brief": payload.brief,
            }
            async with llm_text_rate_limiter():
                candidate = await llm.generate_copy(
                    campaign=campaign,
                    topic=topic,
                    constraints=constraints,
                )
            copywritings.append(_copy_candidate_payload(candidate, payload))
        return {
            "request_id": _request_id(payload.external_request_id, task.id),
            "copywritings": copywritings,
        }

    async def execute_video_storyboard(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> dict[str, Any]:
        del session
        payload = ExternalAIVideoStoryboardCreate.model_validate(task.payload_json or {})
        campaign = _campaign_from_external_context(
            product_name=payload.product_name,
            campaign={},
            adset={},
            creative={},
            brief=payload.brief,
            country=None,
            event_name=None,
            custom_event_type=None,
            audience=None,
            landing_url=None,
            language=payload.language,
            work_order_type=None,
        )
        draft = _draft_from_storyboard_payload(campaign.id, payload)
        assets = _assets_from_storyboard_payload(campaign.id, draft.id, payload)
        async with llm_text_rate_limiter():
            storyboard = await get_llm_provider().generate_video_storyboard(
                campaign=campaign,
                draft=draft,
                assets=assets,
                duration_seconds=payload.duration_seconds,
                aspect_ratio=payload.aspect_ratio,
                context=_storyboard_context(payload),
                instructions=_first_text(payload.prompt, payload.brief),
            )
        return {
            "request_id": _request_id(payload.external_request_id, task.id),
            "storyboard_text": _format_external_storyboard_text(storyboard),
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
        }

    async def _create_job(
        self,
        session: AsyncSession,
        *,
        task_type: str,
        payload: dict[str, Any],
        external_request_id: str | None,
    ) -> GenerationTask:
        request_id = _clean_text(external_request_id)
        if request_id:
            async with ExternalAIIdempotencyLock(task_type, request_id) as acquired:
                if not acquired:
                    existing = await self._find_existing_task_with_wait(
                        session,
                        task_type,
                        request_id,
                    )
                    if existing is not None:
                        existing.reused_existing = True
                        return existing
                    raise AppError("duplicate external_request_id is being created; retry shortly")
                return await self._create_job_unlocked(
                    session,
                    task_type=task_type,
                    payload=payload,
                    external_request_id=request_id,
                )
        return await self._create_job_unlocked(
            session,
            task_type=task_type,
            payload=payload,
            external_request_id=request_id,
        )

    async def _create_job_unlocked(
        self,
        session: AsyncSession,
        *,
        task_type: str,
        payload: dict[str, Any],
        external_request_id: str | None,
    ) -> GenerationTask:
        request_id = _clean_text(external_request_id)
        existing = await self._find_existing_task(session, task_type, request_id)
        if existing is not None:
            existing.reused_existing = True
            return existing

        task = await self.task_service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type=task_type,
            business_type=EXTERNAL_AI_BUSINESS_TYPE,
            business_id=request_id or str(uuid4()),
            campaign_id=None,
            payload=payload,
            metadata={
                "source": EXTERNAL_AI_GENERATION_SOURCE,
                "external_request_id": request_id,
            },
        )
        return task

    async def _find_existing_task_with_wait(
        self,
        session: AsyncSession,
        task_type: str,
        external_request_id: str,
    ) -> GenerationTask | None:
        for _ in range(20):
            existing = await self._find_existing_task(session, task_type, external_request_id)
            if existing is not None:
                return existing
            await asyncio.sleep(0.05)
        return None

    async def _find_existing_task(
        self,
        session: AsyncSession,
        task_type: str,
        external_request_id: str | None,
    ) -> GenerationTask | None:
        if not external_request_id:
            return None
        result = await session.execute(
            select(GenerationTask)
            .where(GenerationTask.task_type == task_type)
            .order_by(GenerationTask.created_at.desc())
        )
        for task in result.scalars().all():
            metadata = task.metadata_json or {}
            if metadata.get("external_request_id") == external_request_id:
                return task
        return None


def _campaign_from_external_context(
    *,
    product_name: str,
    campaign: dict[str, Any],
    adset: dict[str, Any],
    creative: dict[str, Any],
    brief: str | None,
    country: str | None,
    event_name: str | None,
    custom_event_type: str | None,
    audience: str | None,
    landing_url: str | None,
    language: str | None,
    work_order_type: str | None,
) -> Campaign:
    campaign_id = str(uuid4())
    resolved_country = _first_text(country, adset.get("countries"), adset.get("country_code"))
    resolved_event = _first_text(
        event_name,
        custom_event_type,
        adset.get("customEventType"),
        adset.get("custom_event_type"),
        adset.get("event_name"),
        campaign.get("objective"),
    )
    resolved_landing_url = _first_text(
        landing_url,
        creative.get("link"),
        creative.get("landing_url"),
        creative.get("material_url"),
        creative.get("file_url"),
    )
    resolved_audience = _first_text(
        audience,
        adset.get("audience"),
        adset.get("audience_description"),
    )
    parsed_fields = {
        "product_name": product_name,
        "country": resolved_country,
        "event_name": resolved_event,
        "landing_url": resolved_landing_url,
        "audience_description": resolved_audience,
        "brief": brief,
        "language": language,
        "work_order_type": work_order_type,
    }
    metadata_json = {
        "source": EXTERNAL_AI_GENERATION_SOURCE,
        "campaign": campaign,
        "adset": adset,
        "creative": creative,
        "work_order": {
            "parsed_fields": {
                key: value for key, value in parsed_fields.items() if value not in (None, "")
            },
            "country": resolved_country,
            "landing_url": resolved_landing_url,
        },
        "landing_page": (
            {"url": resolved_landing_url, "status": "provided"} if resolved_landing_url else {}
        ),
        "creative_strategy": build_creative_strategy(
            {
                "product_name": product_name,
                "project_name": _first_text(campaign.get("name"), product_name),
                "landing_url": resolved_landing_url,
                "event_name": resolved_event,
                "country": resolved_country,
                "audience_description": resolved_audience,
                "brief": brief,
                "work_order_type": work_order_type,
                "structured_fields": parsed_fields,
            }
        ),
    }
    return Campaign(
        id=campaign_id,
        name=_trim(_first_text(campaign.get("name"), product_name), 255),
        objective=resolved_event,
        product_name=product_name,
        audience_description=resolved_audience,
        metadata_json=metadata_json,
    )


def _topic_signals(payload: ExternalAITopicSelectionCreate) -> dict[str, Any]:
    return {
        "integration": EXTERNAL_AI_GENERATION_SOURCE,
        "brief": payload.brief,
        "work_order_type": payload.work_order_type or payload.industry,
        "work_order": {
            "country": payload.country,
            "parsed_fields": {
                "product_name": payload.product_name,
                "brief": payload.brief,
                "country": payload.country,
                "work_order_type": payload.work_order_type or payload.industry,
                "language": payload.language,
            },
        },
        "product_signals": {"selling_points": []},
        "campaign": payload.campaign,
        "adset": payload.adset,
        "creative": payload.creative,
    }


def _topic_from_copy_payload(
    campaign_id: str,
    payload: ExternalAICopyGenerationCreate,
) -> ContentTopic:
    selling_points = [point for point in payload.selling_points if _clean_text(point)]
    return ContentTopic(
        id=str(uuid4()),
        campaign_id=campaign_id,
        title=_trim(_first_text(payload.brief, payload.product_name), 255),
        angle=_first_text(payload.brief, "External copy generation") or "External copy generation",
        audience=payload.audience,
        selling_points=selling_points,
        rationale="External AI copy generation request.",
        source_data={
            "source": EXTERNAL_AI_GENERATION_SOURCE,
            "campaign": payload.campaign,
            "adset": payload.adset,
            "creative": payload.creative,
        },
    )


def _draft_from_storyboard_payload(
    campaign_id: str,
    payload: ExternalAIVideoStoryboardCreate,
) -> CopyDraft:
    body = _first_text(payload.brief, payload.prompt, payload.product_name) or payload.product_name
    return CopyDraft(
        id=str(uuid4()),
        campaign_id=campaign_id,
        topic_id=str(uuid4()),
        body=body,
        primary_text=body,
        headline=payload.product_name,
        description=payload.brief,
        cta="LEARN_MORE",
        metadata_json={"source": EXTERNAL_AI_GENERATION_SOURCE},
    )


def _assets_from_storyboard_payload(
    campaign_id: str,
    draft_id: str,
    payload: ExternalAIVideoStoryboardCreate,
) -> list[CreativeAsset]:
    assets: list[CreativeAsset] = []
    for index, image_url in enumerate(payload.image_urls, start=1):
        if not _clean_text(image_url):
            continue
        assets.append(
            CreativeAsset(
                id=str(uuid4()),
                campaign_id=campaign_id,
                draft_id=draft_id,
                kind="image",
                url=image_url,
                prompt=_first_text(payload.prompt, payload.brief, payload.product_name)
                or payload.product_name,
                alt_text=f"reference_image_{index}",
                size=payload.aspect_ratio,
                metadata_json={
                    "source": EXTERNAL_AI_GENERATION_SOURCE,
                    "image_index": index,
                },
            )
        )
    return assets


def _storyboard_context(payload: ExternalAIVideoStoryboardCreate) -> dict[str, Any]:
    return {
        "source": EXTERNAL_AI_GENERATION_SOURCE,
        "brief": payload.brief,
        "prompt": payload.prompt,
        "image_urls": payload.image_urls,
        "target_language": payload.language,
    }


def _topic_candidate_payload(candidate: TopicCandidate) -> dict[str, Any]:
    return {
        "title": candidate.title,
        "angle": candidate.angle,
        "angle_type": candidate.angle_type,
        "audience": candidate.audience,
        "selling_points": candidate.selling_points,
        "rationale": candidate.rationale,
    }


def _copy_candidate_payload(
    candidate: CopyDraftCandidate,
    payload: ExternalAICopyGenerationCreate,
) -> dict[str, Any]:
    cta = _first_text(candidate.cta, _copy_cta(payload), "LEARN_MORE") or "LEARN_MORE"
    call_to_action = _normalize_cta(cta)
    event_type = _first_text(payload.customEventType, custom_event_type(payload.event_name))
    return {
        "primary_text": _first_text(candidate.primary_text, candidate.body) or "",
        "headline": candidate.headline,
        "description": candidate.description,
        "cta": cta,
        "call_to_action": call_to_action,
        "customEventType": event_type,
        "copywriting": {
            "primary_text": _first_text(candidate.primary_text, candidate.body) or "",
            "headline": candidate.headline,
            "description": candidate.description,
            "cta": cta,
        },
    }


def _format_external_storyboard_text(storyboard: VideoStoryboardCandidate) -> str:
    blocks: list[str] = []
    for index, scene in enumerate(storyboard.scenes, start=1):
        scene_index = scene.scene_index or index
        blocks.append(
            "\n".join(
                [
                    f"第{scene_index}幕",
                    f"画面：{scene.visual or '-'}",
                    f"字幕：{scene.subtitle or '-'}",
                    f"镜头：{scene.motion or '-'}",
                    f"旁白：{scene.voiceover or '-'}",
                ]
            )
        )
    return "\n\n".join(blocks)


def _request_id(external_request_id: str | None, task_id: str) -> str:
    return _clean_text(external_request_id) or task_id


def _copy_cta(payload: ExternalAICopyGenerationCreate) -> str:
    creative = _dict_or_empty(payload.creative)
    constraints = _dict_or_empty(payload.constraints)
    return (
        _first_text(
            constraints.get("cta"),
            constraints.get("call_to_action"),
            creative.get("btn_type"),
            creative.get("call_to_action"),
        )
        or "LEARN_MORE"
    )


def _default_btn_type(event_type: str | None) -> str:
    if event_type in {"PURCHASE", "ADD_TO_CART", "INITIATED_CHECKOUT"}:
        return "SHOP_NOW"
    if event_type == "COMPLETE_REGISTRATION":
        return "SIGN_UP"
    return "LEARN_MORE"


def _normalize_cta(value: str) -> str:
    text = value.strip()
    if not text:
        return "LEARN_MORE"
    normalized = text.upper().replace(" ", "_").replace("-", "_")
    aliases = {
        "LEARNMORE": "LEARN_MORE",
        "SHOPNOW": "SHOP_NOW",
        "SIGNUP": "SIGN_UP",
        "CONTACTUS": "CONTACT_US",
        "APPLYNOW": "APPLY_NOW",
    }
    return aliases.get(normalized.replace("_", ""), normalized)


def _missing_fields(landing_url: str | None, country_code: str | None) -> list[str]:
    missing = []
    if not landing_url:
        missing.append("landing_url")
    if not country_code:
        missing.append("country")
    return missing


def _low_confidence_fields(
    llm_fields: dict[str, Any],
    structured_fields: dict[str, Any],
) -> list[str]:
    low_confidence: list[str] = []
    for field_name, field in llm_fields.items():
        if structured_fields.get(field_name) not in (None, ""):
            continue
        if isinstance(field, dict) and float(field.get("confidence") or 0) < 0.7:
            low_confidence.append(field_name)
    return low_confidence


def _age_value(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    digits = "".join(character for character in str(value) if character.isdigit())
    if not digits:
        return default
    return max(13, min(65, int(digits[:3])))


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _trim(value: str | None, max_chars: int) -> str:
    text = (value or "").strip()
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    return text[: max_chars - 3].rstrip() + "..."
