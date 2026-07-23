import asyncio
import re
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppError, NotFoundError, ProviderError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.models.topic import ContentTopic
from backend.app.integrations.llm import get_llm_provider
from backend.app.schemas.ai import (
    CopyDraftCandidate,
    DirectorActionCoverageReview,
    FrameAnalysis,
    FrameAnchoredStoryboard,
    TopicCandidate,
    VideoStoryboardCandidate,
    validate_director_coverage,
)
from backend.app.schemas.external_ai_generation import (
    ExternalAICopyGenerationCreate,
    ExternalAIFrameAnchoredStoryboardCreate,
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
from backend.app.services.storyboard_director_coverage_service import (
    review_director_action_coverage,
    validate_final_storyboard_action_coverage,
)
from backend.app.services.storyboard_reference_video_service import (
    PreparedReferenceVideo,
    StoryboardReferenceVideoService,
    adapt_reference_behavior_timeline,
)
from backend.app.services.storyboard_v2_compiler import compile_storyboard_v2
from backend.app.services.work_order_parser import parse_work_order_text
from backend.app.services.work_order_service import WorkOrderService

EXTERNAL_AI_GENERATION_SOURCE = "external_ai_generation"
EXTERNAL_AI_BUSINESS_TYPE = "external_ai"
EXTERNAL_WORK_ORDER_ANALYSIS_TASK_TYPE = "external_work_order_analysis"
EXTERNAL_TOPIC_SELECTION_TASK_TYPE = "external_topic_selection"
EXTERNAL_COPY_GENERATION_TASK_TYPE = "external_copy_generation"
EXTERNAL_VIDEO_STORYBOARD_TASK_TYPE = "external_video_storyboard"
EXTERNAL_VIDEO_STORYBOARD_V2_TASK_TYPE = "external_video_storyboard_v2"
EXTERNAL_AI_TEXT_TASK_TYPES = {
    EXTERNAL_WORK_ORDER_ANALYSIS_TASK_TYPE,
    EXTERNAL_TOPIC_SELECTION_TASK_TYPE,
    EXTERNAL_COPY_GENERATION_TASK_TYPE,
    EXTERNAL_VIDEO_STORYBOARD_TASK_TYPE,
    EXTERNAL_VIDEO_STORYBOARD_V2_TASK_TYPE,
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

    async def create_frame_anchored_video_storyboard_job(
        self,
        session: AsyncSession,
        payload: ExternalAIFrameAnchoredStoryboardCreate,
    ) -> GenerationTask:
        return await self._create_job(
            session,
            task_type=EXTERNAL_VIDEO_STORYBOARD_V2_TASK_TYPE,
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
        if task.task_type == EXTERNAL_VIDEO_STORYBOARD_V2_TASK_TYPE:
            return await self.execute_frame_anchored_video_storyboard(session, task)
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

    async def execute_frame_anchored_video_storyboard(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> dict[str, Any]:
        payload = ExternalAIFrameAnchoredStoryboardCreate.model_validate(task.payload_json or {})
        llm = get_llm_provider()
        reference_service = StoryboardReferenceVideoService()
        prepared_reference: PreparedReferenceVideo | None = None
        frame_analysis = _cached_frame_anchored_analysis(task)
        try:
            if frame_analysis is None:
                if payload.reference_video is not None:
                    prepared_reference = await reference_service.prepare(
                        session,
                        payload.reference_video,
                        task_id=task.id,
                    )
                async with llm_text_rate_limiter():
                    frame_analysis = await llm.analyze_video_frame_pair(
                        first_frame_image_url=payload.first_frame_image_url,
                        last_frame_image_url=payload.last_frame_image_url,
                        duration_seconds=payload.duration_seconds,
                        aspect_ratio=payload.aspect_ratio,
                        reference_frames=(
                            prepared_reference.frames if prepared_reference is not None else None
                        ),
                        reference_video_duration_seconds=(
                            prepared_reference.duration_seconds
                            if prepared_reference is not None
                            else None
                        ),
                        reference_video_sample_interval_seconds=(
                            prepared_reference.sample_interval_seconds
                            if prepared_reference is not None
                            else None
                        ),
                    )
                if (
                    payload.reference_video is not None
                    and frame_analysis.reference_video_analysis is None
                ):
                    raise ProviderError("Visual model did not return reference video analysis.")
                if frame_analysis.reference_video_analysis is not None:
                    frame_analysis = frame_analysis.model_copy(
                        update={
                            "timeline_adaptation_plan": adapt_reference_behavior_timeline(
                                frame_analysis.reference_video_analysis,
                                target_duration_seconds=payload.duration_seconds,
                            )
                        }
                    )
                await _store_frame_anchored_private_metadata(
                    session,
                    task,
                    frame_analysis=frame_analysis.model_dump(mode="json"),
                )

            director_plan_added = False
            if frame_analysis.director_plan is None:
                async with llm_text_rate_limiter():
                    director_plan = await llm.direct_frame_anchored_video_storyboard(
                        first_frame_image_url=payload.first_frame_image_url,
                        last_frame_image_url=payload.last_frame_image_url,
                        frame_analysis=frame_analysis,
                        duration_seconds=payload.duration_seconds,
                        aspect_ratio=payload.aspect_ratio,
                    )
                frame_analysis = frame_analysis.model_copy(
                    update={"director_plan": director_plan}
                )
                director_plan_added = True

            try:
                normalized_frame_analysis = _normalize_private_storyboard_namespace(
                    frame_analysis
                )
            except ValidationError as exc:
                invalid_plan = frame_analysis.director_plan
                if invalid_plan is not None:
                    invalid_review = review_director_action_coverage(
                        frame_analysis,
                        invalid_plan,
                    )
                    if invalid_review.status == "unrecoverable":
                        await _store_frame_anchored_private_metadata(
                            session,
                            task,
                            director_action_coverage_review=invalid_review.model_dump(
                                mode="json"
                            ),
                        )
                        raise ProviderError(
                            "Director plan is unrecoverable before storyboard generation because "
                            f"{_public_unrecoverable_director_reason(invalid_review)}."
                        ) from exc
                raise ProviderError(
                    "Director plan is unrecoverable before storyboard generation because "
                    "director action coverage contract is invalid."
                ) from exc
            if director_plan_added or normalized_frame_analysis != frame_analysis:
                frame_analysis = normalized_frame_analysis
                await _store_frame_anchored_private_metadata(
                    session,
                    task,
                    frame_analysis=frame_analysis.model_dump(mode="json"),
                )

            director_plan = frame_analysis.director_plan
            if director_plan is None:
                raise ProviderError("Frame-anchored director plan is missing.")
            director_review = review_director_action_coverage(frame_analysis, director_plan)
            await _store_frame_anchored_private_metadata(
                session,
                task,
                director_action_coverage_review=director_review.model_dump(mode="json"),
            )
            if director_review.status == "unrecoverable":
                raise ProviderError(
                    "Director plan is unrecoverable before storyboard generation because "
                    f"{_public_unrecoverable_director_reason(director_review)}."
                )

            async with llm_text_rate_limiter():
                storyboard_draft = await llm.generate_frame_anchored_video_storyboard(
                    first_frame_image_url=payload.first_frame_image_url,
                    last_frame_image_url=payload.last_frame_image_url,
                    frame_analysis=frame_analysis,
                    duration_seconds=payload.duration_seconds,
                    aspect_ratio=payload.aspect_ratio,
                    director_corrections=director_review.structured_corrections,
                )
            await _store_frame_anchored_private_metadata(
                session,
                task,
                storyboard_candidate=storyboard_draft.model_dump(mode="json"),
            )
            try:
                storyboard = compile_storyboard_v2(
                    storyboard_draft,
                    frame_analysis,
                    duration_seconds=payload.duration_seconds,
                    aspect_ratio=payload.aspect_ratio,
                )
            except (ValidationError, ValueError) as exc:
                raise ProviderError(
                    "LLM returned a frame-anchored storyboard draft that could not be compiled."
                ) from exc
            try:
                validate_director_coverage(storyboard, director_plan)
                validate_final_storyboard_action_coverage(
                    storyboard,
                    frame_analysis,
                    director_review,
                )
            except ValueError as exc:
                raise ProviderError(
                    "LLM storyboard does not execute the required director action and "
                    "final-anchor return."
                ) from exc
            await _store_frame_anchored_private_metadata(
                session,
                task,
                storyboard=storyboard.model_dump(mode="json"),
            )
            return {
                "request_id": _request_id(payload.external_request_id, task.id),
                "storyboard_text": _format_frame_anchored_storyboard_text(
                    storyboard,
                    private_sources=(
                        frame_analysis.model_dump(mode="json"),
                        director_plan.model_dump(mode="json"),
                        director_review.model_dump(mode="json"),
                        [
                            correction.model_dump(mode="json")
                            for correction in director_review.structured_corrections
                        ],
                        storyboard_draft.model_dump(mode="json"),
                        storyboard.model_dump(mode="json"),
                    ),
                ),
                "duration_seconds": payload.duration_seconds,
                "aspect_ratio": payload.aspect_ratio,
            }
        finally:
            if prepared_reference is not None:
                await reference_service.cleanup(prepared_reference)

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


def _cached_frame_anchored_analysis(task: GenerationTask) -> FrameAnalysis | None:
    metadata = task.metadata_json or {}
    cached = metadata.get("frame_analysis")
    if not isinstance(cached, dict):
        return None
    try:
        return FrameAnalysis.model_validate(cached)
    except ValueError:
        return None


async def _store_frame_anchored_private_metadata(
    session: AsyncSession,
    task: GenerationTask,
    *,
    frame_analysis: dict[str, Any] | None = None,
    director_action_coverage_review: dict[str, Any] | None = None,
    storyboard_candidate: dict[str, Any] | None = None,
    storyboard: dict[str, Any] | None = None,
) -> None:
    metadata = task.metadata_json or {}
    if frame_analysis is not None:
        metadata = {**metadata, "frame_analysis": frame_analysis}
    if director_action_coverage_review is not None:
        metadata = {
            **metadata,
            "director_action_coverage_review": director_action_coverage_review,
        }
    if storyboard_candidate is not None:
        metadata = {
            **metadata,
            "frame_anchored_storyboard_candidate": storyboard_candidate,
        }
    if storyboard is not None:
        metadata = {**metadata, "frame_anchored_storyboard": storyboard}
    task.metadata_json = metadata
    session.add(task)
    await session.commit()
    await session.refresh(task)


_SAFE_PRIVATE_ID_PATTERN = re.compile(
    r"^(?:__sbv2_(?:behavior|beat|window|moment|claim)_\d+__|"
    r"[A-Za-z0-9]+(?:[_:.-][A-Za-z0-9]+)+)$"
)


def _private_namespace_map(values: list[str], namespace: str) -> dict[str, str]:
    normalized_values = [value.strip() for value in values if value.strip()]
    reserved_pattern = re.compile(rf"^__sbv2_{re.escape(namespace)}_\d+__$")
    reserved_ids = {
        value for value in normalized_values if reserved_pattern.fullmatch(value)
    }
    mapping: dict[str, str] = {}
    used_ids = set(reserved_ids)
    next_index = 1
    for clean in normalized_values:
        if clean in mapping:
            continue
        if clean in reserved_ids:
            mapping[clean] = clean
            continue
        while True:
            candidate = f"__sbv2_{namespace}_{next_index:03d}__"
            next_index += 1
            if candidate not in used_ids:
                break
        mapping[clean] = candidate
        used_ids.add(candidate)
    return mapping


def _mapped_private_id(value: str | None, mapping: dict[str, str]) -> str | None:
    if value is None:
        return None
    clean = value.strip()
    return mapping.get(clean, clean)


def _replace_private_ids_in_text(value: str, mapping: dict[str, str]) -> str:
    replacements = {
        original: replacement
        for original, replacement in mapping.items()
        if original and original != replacement
    }
    if not replacements:
        return value
    aliases = sorted(replacements, key=len, reverse=True)
    alias_pattern = "|".join(re.escape(alias) for alias in aliases)
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_])(?<![A-Za-z0-9][_:.-])"
        rf"(?:{alias_pattern})"
        rf"(?![A-Za-z0-9_])(?![_:.-][A-Za-z0-9])"
    )
    return pattern.sub(lambda match: replacements[match.group(0)], value)


def _replace_private_id_aliases(value: Any, mapping: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                _mapped_private_id(item, mapping)
                if key == "claim_id" and (item is None or isinstance(item, str))
                else _replace_private_id_aliases(item, mapping)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_private_id_aliases(item, mapping) for item in value]
    if not isinstance(value, str):
        return value
    return _replace_private_ids_in_text(value, mapping)


def _normalize_private_storyboard_claim_namespace(
    storyboard: FrameAnchoredStoryboard,
) -> FrameAnchoredStoryboard:
    claim_values = [
        evidence.claim_id
        for scene in storyboard.scenes
        for evidence in scene.execution_evidence
    ]
    claim_map = _private_namespace_map(claim_values, "claim")
    normalized_payload = _replace_private_id_aliases(
        storyboard.model_dump(mode="python"),
        claim_map,
    )
    return FrameAnchoredStoryboard.model_validate(normalized_payload)


def _normalize_private_storyboard_namespace(frame_analysis: FrameAnalysis) -> FrameAnalysis:
    reference = frame_analysis.reference_video_analysis
    reference_graph = reference.behavior_graph if reference is not None else None
    timeline = frame_analysis.timeline_adaptation_plan
    plan = frame_analysis.director_plan

    behavior_values: list[str] = []
    if reference_graph is not None:
        for beat in reference_graph.beats:
            behavior_values.extend([beat.beat_id, *beat.depends_on])
    if timeline is not None:
        for beat in timeline.beats:
            behavior_values.extend([beat.beat_id, *beat.depends_on])
    if plan is not None:
        for moment in plan.signature_moment_plan:
            behavior_values.extend(moment.source_behavior_beat_ids)
    behavior_map = _private_namespace_map(behavior_values, "behavior")

    director_values: list[str] = []
    window_values: list[str] = []
    moment_values: list[str] = []
    if plan is not None:
        for beat in plan.climax_beats:
            director_values.extend([beat.beat_id, *beat.depends_on])
        for window in plan.action_arc_windows:
            window_values.extend([window.window_id, *window.depends_on])
        for moment in plan.signature_moment_plan:
            moment_values.append(moment.moment_id)
            if moment.assigned_beat_id:
                director_values.append(moment.assigned_beat_id)
    director_map = _private_namespace_map(director_values, "beat")
    window_map = _private_namespace_map(window_values, "window")
    moment_map = _private_namespace_map(moment_values, "moment")

    normalized_reference = reference
    if reference is not None and reference_graph is not None:
        normalized_reference = reference.model_copy(
            update={
                "behavior_graph": reference_graph.model_copy(
                    update={
                        "beats": [
                            beat.model_copy(
                                update={
                                    "beat_id": _mapped_private_id(
                                        beat.beat_id, behavior_map
                                    ),
                                    "depends_on": [
                                        _mapped_private_id(dependency, behavior_map)
                                        for dependency in beat.depends_on
                                    ],
                                }
                            )
                            for beat in reference_graph.beats
                        ]
                    }
                )
            }
        )

    normalized_timeline = timeline
    if timeline is not None:
        normalized_timeline = timeline.model_copy(
            update={
                "beats": [
                    beat.model_copy(
                        update={
                            "beat_id": _mapped_private_id(beat.beat_id, behavior_map),
                            "depends_on": [
                                _mapped_private_id(dependency, behavior_map)
                                for dependency in beat.depends_on
                            ],
                        }
                    )
                    for beat in timeline.beats
                ]
            }
        )

    normalized_plan = plan
    if plan is not None:
        normalized_plan = plan.model_copy(
            update={
                "climax_beats": [
                    beat.model_copy(
                        update={
                            "beat_id": _mapped_private_id(beat.beat_id, director_map),
                            "depends_on": [
                                _mapped_private_id(dependency, director_map)
                                for dependency in beat.depends_on
                            ],
                        }
                    )
                    for beat in plan.climax_beats
                ],
                "action_arc_windows": [
                    window.model_copy(
                        update={
                            "window_id": _mapped_private_id(window.window_id, window_map),
                            "depends_on": [
                                _mapped_private_id(dependency, window_map)
                                for dependency in window.depends_on
                            ],
                        }
                    )
                    for window in plan.action_arc_windows
                ],
                "signature_moment_plan": [
                    moment.model_copy(
                        update={
                            "moment_id": _mapped_private_id(moment.moment_id, moment_map),
                            "source_behavior_beat_ids": [
                                _mapped_private_id(beat_id, behavior_map)
                                for beat_id in moment.source_behavior_beat_ids
                            ],
                            "assigned_beat_id": _mapped_private_id(
                                moment.assigned_beat_id, director_map
                            ),
                        }
                    )
                    for moment in plan.signature_moment_plan
                ],
            }
        )

    normalized = frame_analysis.model_copy(
        update={
            "reference_video_analysis": normalized_reference,
            "timeline_adaptation_plan": normalized_timeline,
            "director_plan": normalized_plan,
        }
    )
    return FrameAnalysis.model_validate(normalized.model_dump(mode="python"))


def _public_unrecoverable_director_reason(
    review: DirectorActionCoverageReview,
) -> str:
    normalized_reasons = [reason.casefold() for reason in review.unrecoverable_reasons]
    if review.invalid_omission_moment_ids or any(
        "invalid omission contract" in reason for reason in normalized_reasons
    ):
        return "invalid omission contract"
    if any("signature/source linkage" in reason for reason in normalized_reasons):
        return "required signature/source linkage is missing"
    return "director action coverage contract is invalid"


def _private_id_values(value: Any, *, field_name: str | None = None) -> set[str]:
    private_ids: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            private_ids.update(_private_id_values(item, field_name=str(key)))
        return private_ids
    if isinstance(value, (list, tuple, set)):
        for item in value:
            private_ids.update(_private_id_values(item, field_name=field_name))
        return private_ids
    is_private_id_field = bool(
        field_name
        and (
            field_name.endswith("_id")
            or field_name.endswith("_ids")
            or field_name in {"depends_on", "cinematic_beat", "cinematic_beats"}
        )
    )
    if is_private_id_field and isinstance(value, str) and value.strip():
        private_ids.add(value.strip())
    return private_ids


def _private_storyboard_ids(
    storyboard: FrameAnchoredStoryboard,
    private_sources: tuple[Any, ...] = (),
) -> tuple[str, ...]:
    private_ids = {
        private_id
        for private_id in _private_id_values(storyboard.model_dump(mode="json"))
        if _SAFE_PRIVATE_ID_PATTERN.fullmatch(private_id)
    }
    for source in private_sources:
        private_ids.update(
            private_id
            for private_id in _private_id_values(source)
            if _SAFE_PRIVATE_ID_PATTERN.fullmatch(private_id)
        )
    return tuple(sorted(private_ids, key=len, reverse=True))


def _scrub_private_storyboard_ids(value: str | None, private_ids: tuple[str, ...]) -> str:
    return _replace_private_ids_in_text(
        value or "",
        {private_id: "linked item" for private_id in private_ids},
    )


def _format_frame_anchored_storyboard_text(
    storyboard: FrameAnchoredStoryboard,
    *,
    private_sources: tuple[Any, ...] = (),
) -> str:
    private_ids = _private_storyboard_ids(storyboard, private_sources)

    def clean(value: str | None) -> str:
        return _scrub_private_storyboard_ids(value, private_ids)

    blocks = [
        f"Duration: {storyboard.duration_seconds}s",
        f"Aspect ratio: {storyboard.aspect_ratio}",
    ]
    for index, scene in enumerate(storyboard.scenes, start=1):
        scene_index = scene.scene_index or index
        timing = _scene_timing(scene.start_second, scene.end_second)
        lines = [
            f"Scene {scene_index} ({timing})",
            f"Anchor: {scene.frame_anchor}",
            f"Visual: {clean(scene.visual)}",
            f"Tension stage: {clean(scene.tension_stage) or '-'}",
            f"Camera and motion: {clean(scene.motion) or '-'}",
            f"Camera instruction: {clean(scene.camera_instruction) or '-'}",
            f"Transition goal: {clean(scene.transition_goal) or '-'}",
            f"Action-result requirement: {clean(scene.action_result_requirement) or '-'}",
            f"Effect timing: {clean(scene.effect_timing) or '-'}",
            (
                "Subject motion intensity: "
                f"{_format_optional_intensity(scene.subject_motion_intensity)}"
            ),
            f"Camera intensity: {_format_optional_intensity(scene.camera_intensity)}",
            f"Effect intensity: {_format_optional_intensity(scene.effect_intensity)}",
            f"Return to final anchor: {clean(scene.anchor_return_instruction) or '-'}",
            f"Anti-flattening requirement: {clean(scene.anti_flattening_requirement) or '-'}",
            f"Subtitle: {clean(scene.subtitle) or '-'}",
            f"Voiceover: {clean(scene.voiceover) or '-'}",
            f"Sound effects: {', '.join(clean(item) for item in scene.sound_effects) or '-'}",
        ]
        if scene.overlay_instruction is not None:
            overlay = scene.overlay_instruction
            if isinstance(overlay, str):
                lines.append(f"Overlay lifecycle: {clean(overlay)}")
            else:
                lines.append(
                    "Overlay lifecycle: "
                    f"{clean(overlay.reference_element)} -> {overlay.strategy}; "
                    f"timing: {clean(overlay.timing_instruction)}; "
                    f"final frame: {clean(overlay.final_frame_requirement)}"
                )
        if scene.notes:
            lines.append(f"Notes: {clean(scene.notes)}")
        blocks.append("\n".join(lines))
    blocks.extend(
        [
            f"Music: {clean(storyboard.sound_design.music) or '-'}",
            f"Ambience: {clean(storyboard.sound_design.ambience) or '-'}",
        ]
    )
    if storyboard.rationale:
        blocks.append(f"Overall direction: {clean(storyboard.rationale)}")
    return "\n\n".join(blocks)


def _format_optional_intensity(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _scene_timing(start_second: float | None, end_second: float | None) -> str:
    if start_second is None and end_second is None:
        return "timing not specified"
    start = _format_timeline_second(start_second)
    end = _format_timeline_second(end_second)
    return f"{start}-{end}s"


def _format_timeline_second(value: float | None) -> str:
    if value is None:
        return "?"
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".")


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
