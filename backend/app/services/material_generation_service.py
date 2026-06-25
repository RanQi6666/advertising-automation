from typing import Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppError, ProviderError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import VideoStatus
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.schemas.material_generation import (
    MATERIAL_CODE_BRAND_SAFETY_ERROR,
    MATERIAL_CODE_PROCESSING,
    MATERIAL_CODE_PROVIDER_ERROR,
    MATERIAL_CODE_SUCCESS,
    MATERIAL_CODE_VALIDATION_ERROR,
    MaterialCopyGenerateRequest,
    MaterialGenerationAPIError,
    MaterialGenerationEnvelope,
    MaterialImageGenerateRequest,
    MaterialVideoGenerateRequest,
)
from backend.app.services.brand_safety_policy import scan_brand_safety
from backend.app.services.copywriting_service import CopywritingService
from backend.app.services.creative_service import CreativeService
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.video_service import VideoService

SOURCE = "external_material_generation"


class MaterialGenerationService:
    def __init__(self) -> None:
        self.copywriting = CopywritingService()

    async def generate_copy(
        self,
        session: AsyncSession,
        payload: MaterialCopyGenerateRequest,
    ) -> MaterialGenerationEnvelope:
        _validate_base_payload(payload)

        existing = await self._find_existing_copy(session, payload.external_request_id)
        if existing:
            return self._copy_response(existing, payload)

        try:
            campaign, topic = await self._create_context(session, payload)
            candidate = await self.copywriting.llm.generate_copy(
                campaign=campaign,
                topic=topic,
                constraints=payload.constraints,
            )
            custom_event_type = payload.customEventType or _custom_event_type(payload.event_name)
            draft = CopyDraft(
                campaign_id=campaign.id,
                topic_id=topic.id,
                body=candidate.body,
                primary_text=candidate.primary_text,
                headline=candidate.headline,
                description=candidate.description,
                cta=candidate.cta,
                model_name=self.copywriting.settings.llm_model,
                prompt_version="copywriting.v1",
                metadata_json={
                    "source": SOURCE,
                    "external_request_id": payload.external_request_id,
                    "material_type": "copy",
                    "customEventType": custom_event_type,
                },
            )
            session.add(draft)
            await session.flush()

            response = self._copy_response(draft, payload)
            self._raise_if_brand_safety_blocked(response.data)

            await session.commit()
            await session.refresh(draft)
            return self._copy_response(draft, payload)
        except MaterialGenerationAPIError:
            await session.rollback()
            raise

    async def generate_images(
        self,
        session: AsyncSession,
        payload: MaterialImageGenerateRequest,
    ) -> MaterialGenerationEnvelope:
        _validate_base_payload(payload)

        existing = await self._find_existing_images(session, payload.external_request_id)
        if existing:
            return self._image_response(existing)

        copy_response = await self.generate_copy(session, payload)
        draft_id = copy_response.data["request_id"]
        creative_service = self._creative_service()
        image_metadata = {
            "streamed": False,
            "source": SOURCE,
            "external_request_id": payload.external_request_id,
            "material_type": "image",
        }
        assets = await creative_service.build_creative_assets_without_commit(
            session=session,
            draft_id=draft_id,
            count=payload.count,
            size=payload.size,
            extra_metadata=image_metadata,
        )

        try:
            for asset in assets:
                self._raise_if_brand_safety_blocked(
                    {"prompt": asset.prompt, "alt_text": asset.alt_text}
                )
                session.add(asset)
            await session.commit()
            for asset in assets:
                await session.refresh(asset)
            return self._image_response(assets)
        except MaterialGenerationAPIError:
            await session.rollback()
            raise

    def _creative_service(self) -> CreativeService:
        return CreativeService()

    async def create_video(
        self,
        session: AsyncSession,
        payload: MaterialVideoGenerateRequest,
    ) -> MaterialGenerationEnvelope:
        _validate_base_payload(payload)
        if not payload.image_urls:
            raise MaterialGenerationAPIError(
                "image_urls is required",
                code=MATERIAL_CODE_VALIDATION_ERROR,
            )

        existing = await self._find_existing_video(session, payload.external_request_id)
        if existing:
            return self._video_response(existing)

        try:
            campaign, topic = await self._create_context(session, payload, material_type="video")
            draft = CopyDraft(
                campaign_id=campaign.id,
                topic_id=topic.id,
                body=payload.brief or "Create a short video for daily use.",
                primary_text=payload.brief or "Create a short video for daily use.",
                headline=payload.product_name,
                description=payload.brief,
                cta="Learn More",
                model_name="material-generation-placeholder",
                prompt_version="material.video.v1",
                metadata_json={
                    "source": SOURCE,
                    "external_request_id": payload.external_request_id,
                    "material_type": "video",
                    "customEventType": payload.customEventType
                    or _custom_event_type(payload.event_name),
                },
            )
            session.add(draft)
            await session.flush()

            image_storage = ImageStorageService()
            source_assets: list[CreativeAsset] = []
            for image_url in payload.image_urls:
                url = str(image_url)
                asset = CreativeAsset(
                    campaign_id=campaign.id,
                    draft_id=draft.id,
                    kind="image",
                    url=url,
                    storage_key=image_storage.storage_key_for_public_url(url),
                    prompt=payload.prompt or payload.brief or "",
                    alt_text=payload.product_name,
                    size=payload.aspect_ratio,
                    metadata_json={
                        "source": SOURCE,
                        "external_request_id": payload.external_request_id,
                        "material_type": "video_source_image",
                        "provider_image_url": url,
                    },
                )
                session.add(asset)
                source_assets.append(asset)
            await session.flush()

            video = VideoAsset(
                campaign_id=campaign.id,
                draft_id=draft.id,
                source_asset_ids=[asset.id for asset in source_assets],
                prompt=payload.prompt or payload.brief,
                storyboard=[],
                duration_seconds=payload.duration_seconds,
                aspect_ratio=payload.aspect_ratio,
                metadata_json={
                    "source": SOURCE,
                    "external_request_id": payload.external_request_id,
                    "material_type": "video",
                    "implementation_status": "configured",
                    "note": (
                        "Video generation task is configured. "
                        "Provider generation starts immediately."
                    ),
                },
            )
            session.add(video)
            await session.flush()

            started = await self._video_service().start_video_generation(session, video.id)
            return self._video_processing_response(started)
        except MaterialGenerationAPIError:
            await session.rollback()
            raise
        except ProviderError:
            await session.rollback()
            raise
        except AppError:
            await session.rollback()
            raise
        except Exception:
            await session.rollback()
            raise

    async def get_video_job(
        self,
        session: AsyncSession,
        job_id: str,
    ) -> MaterialGenerationEnvelope:
        video = await session.get(VideoAsset, job_id)
        if not video:
            raise MaterialGenerationAPIError(
                "video job not found",
                code=MATERIAL_CODE_VALIDATION_ERROR,
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if video.status == VideoStatus.GENERATING.value and video.provider_job_id:
            video = await self._video_service().refresh_video_generation(session, video.id)

        return self._video_response(video)

    def _video_service(self) -> VideoService:
        return VideoService()

    async def _create_context(
        self,
        session: AsyncSession,
        payload: MaterialCopyGenerateRequest,
        *,
        material_type: str = "copy",
    ) -> tuple[Campaign, ContentTopic]:
        product_name = (payload.product_name or "").strip()
        brief = (payload.brief or "").strip()
        selling_points = [point.strip() for point in payload.selling_points if point.strip()]
        landing_url = str(payload.landing_url) if payload.landing_url else None

        external_context = {
            "source": SOURCE,
            "external_request_id": payload.external_request_id,
            "material_type": material_type,
            "product_name": product_name,
            "landing_url": landing_url,
            "audience": payload.audience,
            "country": payload.country,
            "event_name": payload.event_name,
            "customEventType": payload.customEventType,
            "language": payload.language,
            "brief": brief,
            "selling_points": selling_points,
            "constraints": payload.constraints,
        }
        work_order = {
            "source": SOURCE,
            "landing_url": landing_url,
            "country": payload.country,
            "event_name": payload.event_name,
            "parsed_fields": {
                "landing_url": landing_url,
                "country": payload.country,
                "event_name": payload.event_name,
            },
        }

        campaign = Campaign(
            name=product_name,
            product_name=product_name,
            objective=payload.event_name,
            audience_description=payload.audience,
            metadata_json={**external_context, "work_order": work_order},
        )
        session.add(campaign)
        await session.flush()

        topic = ContentTopic(
            campaign_id=campaign.id,
            title=product_name,
            angle=brief or "Create a clear ad for daily use.",
            audience=payload.audience,
            selling_points=selling_points,
            source_data=external_context,
        )
        session.add(topic)
        await session.flush()
        return campaign, topic

    async def _find_existing_copy(
        self,
        session: AsyncSession,
        external_request_id: str | None,
    ) -> CopyDraft | None:
        if not (external_request_id or "").strip():
            return None

        result = await session.execute(select(CopyDraft).order_by(CopyDraft.created_at.desc()))
        for draft in result.scalars().all():
            metadata = draft.metadata_json or {}
            if (
                metadata.get("source") == SOURCE
                and metadata.get("material_type") == "copy"
                and metadata.get("external_request_id") == external_request_id
            ):
                return draft
        return None

    async def _find_existing_images(
        self,
        session: AsyncSession,
        external_request_id: str | None,
    ) -> list[CreativeAsset]:
        if not (external_request_id or "").strip():
            return []

        result = await session.execute(
            select(CreativeAsset).order_by(CreativeAsset.created_at.asc())
        )
        assets: list[CreativeAsset] = []
        for asset in result.scalars().all():
            metadata = asset.metadata_json or {}
            if (
                metadata.get("source") == SOURCE
                and metadata.get("material_type") == "image"
                and metadata.get("external_request_id") == external_request_id
                and asset.url
            ):
                assets.append(asset)
        return assets

    async def _find_existing_video(
        self,
        session: AsyncSession,
        external_request_id: str | None,
    ) -> VideoAsset | None:
        if not (external_request_id or "").strip():
            return None

        result = await session.execute(select(VideoAsset).order_by(VideoAsset.created_at.desc()))
        for video in result.scalars().all():
            metadata = video.metadata_json or {}
            if (
                metadata.get("source") == SOURCE
                and metadata.get("material_type") == "video"
                and metadata.get("external_request_id") == external_request_id
                and video.status
                in {
                    VideoStatus.GENERATING.value,
                    VideoStatus.REQUESTED.value,
                    VideoStatus.GENERATED.value,
                    VideoStatus.APPROVED.value,
                    VideoStatus.FAILED.value,
                }
            ):
                return video
        return None

    def _copy_response(
        self,
        draft: CopyDraft,
        payload: MaterialCopyGenerateRequest,
    ) -> MaterialGenerationEnvelope:
        metadata = draft.metadata_json or {}
        custom_event_type = metadata.get("customEventType") or metadata.get("custom_event_type")
        return MaterialGenerationEnvelope(
            code=0,
            message="success",
            data={
                "request_id": draft.id,
                "primary_text": draft.primary_text or draft.body,
                "headline": draft.headline,
                "description": draft.description,
                "cta": draft.cta,
                "customEventType": custom_event_type
                or payload.customEventType
                or _custom_event_type(payload.event_name),
            },
        )

    def _image_response(self, assets: list[CreativeAsset]) -> MaterialGenerationEnvelope:
        urls = [asset.url for asset in assets if asset.url]
        if not urls:
            raise MaterialGenerationAPIError(
                "image generation returned no usable urls",
                code=MATERIAL_CODE_PROVIDER_ERROR,
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return MaterialGenerationEnvelope(
            code=0,
            message="success",
            data={"request_id": assets[0].id, "urls": urls},
        )

    def _video_response(self, video: VideoAsset) -> MaterialGenerationEnvelope:
        if video.status in {VideoStatus.GENERATED.value, VideoStatus.APPROVED.value} and video.url:
            return MaterialGenerationEnvelope(
                code=MATERIAL_CODE_SUCCESS,
                message="success",
                data={
                    "job_id": video.id,
                    "status": "succeeded",
                    "url": video.url,
                },
            )
        if video.status == VideoStatus.FAILED.value:
            return MaterialGenerationEnvelope(
                code=MATERIAL_CODE_PROVIDER_ERROR,
                message="video generation failed",
                data={
                    "job_id": video.id,
                    "status": "failed",
                    "error": video.error_message,
                },
            )
        return self._video_processing_response(video)

    def _video_processing_response(self, video: VideoAsset) -> MaterialGenerationEnvelope:
        return MaterialGenerationEnvelope(
            code=MATERIAL_CODE_PROCESSING,
            message="processing",
            data={
                "job_id": video.id,
                "status": "processing",
            },
        )

    def _raise_if_brand_safety_blocked(self, data: dict[str, Any]) -> None:
        result = scan_brand_safety(data)
        if result["status"] != "blocked":
            return
        raise MaterialGenerationAPIError(
            "brand safety policy blocked generated copy",
            code=MATERIAL_CODE_BRAND_SAFETY_ERROR,
            status_code=status.HTTP_409_CONFLICT,
            data={"brand_safety": result},
        )


def _validate_base_payload(payload: MaterialCopyGenerateRequest) -> None:
    if not (payload.product_name or "").strip():
        raise MaterialGenerationAPIError(
            "product_name is required",
            code=MATERIAL_CODE_VALIDATION_ERROR,
        )
    has_brief = bool((payload.brief or "").strip())
    has_selling_points = any(point.strip() for point in payload.selling_points)
    if not has_brief and not has_selling_points:
        raise MaterialGenerationAPIError(
            "brief or selling_points is required",
            code=MATERIAL_CODE_VALIDATION_ERROR,
        )


def _custom_event_type(event_name: str | None) -> str:
    normalized = (event_name or "").strip().lower().replace("-", "_").replace(" ", "_")
    if any(key in normalized for key in ("complete_registration", "registration", "register")):
        return "COMPLETE_REGISTRATION"
    if any(key in normalized for key in ("purchase", "shop", "order")):
        return "PURCHASE"
    if any(key in normalized for key in ("add_to_cart", "cart")):
        return "ADD_TO_CART"
    if "lead" in normalized:
        return "LEAD"
    return "LINK_CLICK"
