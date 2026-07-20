import asyncio
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.core.errors import AppError, ProviderError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import CreativeStatus, VideoStatus
from backend.app.db.models.video_asset import VideoAsset
from backend.app.integrations.llm import get_llm_provider
from backend.app.integrations.video import get_video_provider
from backend.app.integrations.video.base import VideoGenerationRequest, VideoSourceImage
from backend.app.schemas.video import (
    VideoGenerateRequest,
    VideoStoryboardGenerateRequest,
    VideoStoryboardRead,
    VideoStoryboardRewriteRequest,
)
from backend.app.services.creative_asset_urls import (
    repair_creative_asset_urls,
    resolve_creative_image_url,
)
from backend.app.services.creative_safety_prompts import (
    creative_safety_prompt_block,
    sanitize_creative_safety_text,
)
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.landing_page_service import LandingPageService, snapshot_to_context
from backend.app.services.landing_visual_reference import merge_landing_visual_reference
from backend.app.services.llm_rate_limit import llm_text_rate_limiter
from backend.app.services.model_selection import effective_text_model, settings_for_text_model
from backend.app.services.utils import get_required
from backend.app.services.video_final_overlay_service import apply_final_text_overlay_locks
from backend.app.services.video_storage_service import VideoStorageService

VIDEO_STREAM_HEARTBEAT_SECONDS = 5.0
FIXED_GAME_CARD_STYLE_TERMS = (
    "premium cards",
    "game cards",
    "card carousel",
    "fast carousel",
    "end card",
    "hero card",
    "jewel card",
    "game-card",
    "card fan-out",
)


class VideoService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._llm = None
        self._video_provider = None
        self.landing_pages = LandingPageService()
        self.image_storage = ImageStorageService(self.settings)
        self.video_storage = VideoStorageService(self.settings)

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm_provider(self.settings)
        return self._llm

    def _llm_for_model(self, model_id: str | None):
        llm_settings = settings_for_text_model(self.settings, model_id)
        return get_llm_provider(llm_settings), llm_settings

    @property
    def video_provider(self):
        if self._video_provider is None:
            self._video_provider = get_video_provider(self.settings)
        return self._video_provider

    async def generate_storyboard(
        self,
        session: AsyncSession,
        payload: VideoStoryboardGenerateRequest,
    ) -> VideoStoryboardRead:
        campaign = await get_required(session, Campaign, payload.campaign_id)
        assets = await self._load_source_assets(
            session=session,
            campaign_id=payload.campaign_id,
            asset_ids=payload.creative_asset_ids,
        )
        draft = await self._load_draft(session, payload.draft_id, assets)
        context = await self._video_context(session, campaign, draft, assets, payload.metadata_json)
        llm, llm_settings = self._llm_for_model(payload.model_id)
        async with llm_text_rate_limiter():
            storyboard = await llm.generate_video_storyboard(
                campaign=campaign,  # type: ignore[arg-type]
                draft=draft,  # type: ignore[arg-type]
                assets=assets,  # type: ignore[arg-type]
                duration_seconds=payload.duration_seconds,
                aspect_ratio=payload.aspect_ratio,
                context=context,
                instructions=payload.instructions,
            )
        storyboard_items = [scene.model_dump() for scene in storyboard.scenes]
        creative_strategy = _strategy_from_context(context)
        prompt = _storyboard_to_prompt(
            storyboard_items,
            creative_strategy=creative_strategy,
        )
        return VideoStoryboardRead(
            campaign_id=payload.campaign_id,
            draft_id=draft.id if draft else None,
            creative_asset_ids=payload.creative_asset_ids,
            duration_seconds=storyboard.duration_seconds,
            aspect_ratio=storyboard.aspect_ratio,
            storyboard=storyboard_items,
            prompt=prompt,
            metadata_json={
                **payload.metadata_json,
                "rationale": storyboard.rationale,
                "provider": llm_settings.llm_provider,
                "model": effective_text_model(llm_settings),
                "instructions": payload.instructions,
                **({"creative_strategy": creative_strategy} if creative_strategy else {}),
            },
        )

    async def create_video_job(
        self,
        session: AsyncSession,
        payload: VideoGenerateRequest,
    ) -> VideoAsset:
        campaign = await get_required(session, Campaign, payload.campaign_id)
        draft = None
        if payload.draft_id:
            draft = await get_required(session, CopyDraft, payload.draft_id)

        assets = await self._load_source_assets(
            session=session,
            campaign_id=payload.campaign_id,
            asset_ids=payload.creative_asset_ids,
        )
        _validate_video_keyframe_source_assets(assets)
        inferred_draft_id = payload.draft_id or assets[0].draft_id
        if draft is None and inferred_draft_id:
            draft = await get_required(session, CopyDraft, inferred_draft_id)
        landing_page_context = await self._landing_page_context(
            session,
            campaign_id=payload.campaign_id,
        )
        creative_strategy = _creative_strategy_from_sources(
            campaign=campaign,  # type: ignore[arg-type]
            draft=draft,  # type: ignore[arg-type]
            assets=assets,  # type: ignore[arg-type]
            metadata=payload.metadata_json,
        )
        creative_strategy = merge_landing_visual_reference(
            creative_strategy,
            landing_page_context,
        )

        video = VideoAsset(
            campaign_id=payload.campaign_id,
            draft_id=inferred_draft_id,
            source_asset_ids=payload.creative_asset_ids,
            prompt=_prompt_with_creative_strategy(payload.prompt, creative_strategy),
            storyboard=payload.storyboard,
            duration_seconds=payload.duration_seconds,
            aspect_ratio=payload.aspect_ratio,
            metadata_json={
                **payload.metadata_json,
                "implementation_status": "configured",
                "note": "Video generation task is configured. Call generate to start provider job.",
                "landing_page": landing_page_context,
                **({"creative_strategy": creative_strategy} if creative_strategy else {}),
            },
        )
        session.add(video)
        await session.commit()
        await session.refresh(video)
        return video

    async def stream_storyboard_text(
        self,
        session: AsyncSession,
        payload: VideoStoryboardGenerateRequest,
    ) -> AsyncIterator[dict]:
        campaign = await get_required(session, Campaign, payload.campaign_id)
        assets = await self._load_source_assets(
            session=session,
            campaign_id=payload.campaign_id,
            asset_ids=payload.creative_asset_ids,
        )
        draft = await self._load_draft(session, payload.draft_id, assets)
        context = await self._video_context(session, campaign, draft, assets, payload.metadata_json)
        yield {
            "type": "start",
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
        }

        full_text = ""
        try:
            llm, _llm_settings = self._llm_for_model(payload.model_id)
            async with llm_text_rate_limiter():
                async for event in _stream_text_with_heartbeat(
                    llm.stream_video_storyboard_text(
                        campaign=campaign,  # type: ignore[arg-type]
                        draft=draft,  # type: ignore[arg-type]
                        assets=assets,  # type: ignore[arg-type]
                        duration_seconds=payload.duration_seconds,
                        aspect_ratio=payload.aspect_ratio,
                        context=context,
                        instructions=payload.instructions,
                    ),
                    stage="video_storyboard_generation",
                ):
                    if event["type"] == "delta":
                        full_text += event["text"]
                    yield event
        except Exception as exc:
            yield {"type": "error", "message": f"视频脚本生成中断：{exc}"}
            return

        full_text = _ensure_storyboard_source_asset_notes(full_text, assets)
        yield {
            "type": "done",
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
            "text": full_text,
        }

    async def rewrite_storyboard(
        self,
        session: AsyncSession,
        payload: VideoStoryboardRewriteRequest,
    ) -> VideoStoryboardRead:
        feedback = payload.feedback.strip()
        if not feedback:
            raise AppError("Please provide storyboard revision feedback.")
        if not payload.storyboard and not (payload.storyboard_text or "").strip():
            raise AppError("Please generate or enter a storyboard before rewriting it.")

        campaign = await get_required(session, Campaign, payload.campaign_id)
        assets = await self._load_source_assets(
            session=session,
            campaign_id=payload.campaign_id,
            asset_ids=payload.creative_asset_ids,
        )
        draft = await self._load_draft(session, payload.draft_id, assets)
        context = await self._video_context(session, campaign, draft, assets, payload.metadata_json)
        llm, llm_settings = self._llm_for_model(payload.model_id)
        async with llm_text_rate_limiter():
            storyboard = await llm.revise_video_storyboard(
                campaign=campaign,  # type: ignore[arg-type]
                draft=draft,  # type: ignore[arg-type]
                assets=assets,  # type: ignore[arg-type]
                duration_seconds=payload.duration_seconds,
                aspect_ratio=payload.aspect_ratio,
                context=context,
                current_storyboard=payload.storyboard,
                current_storyboard_text=payload.storyboard_text,
                feedback=feedback,
            )
        storyboard_items = [scene.model_dump() for scene in storyboard.scenes]
        creative_strategy = _strategy_from_context(context)
        prompt = _storyboard_to_prompt(
            storyboard_items,
            creative_strategy=creative_strategy,
        )
        return VideoStoryboardRead(
            campaign_id=payload.campaign_id,
            draft_id=draft.id if draft else None,
            creative_asset_ids=payload.creative_asset_ids,
            duration_seconds=storyboard.duration_seconds,
            aspect_ratio=storyboard.aspect_ratio,
            storyboard=storyboard_items,
            prompt=prompt,
            metadata_json={
                **payload.metadata_json,
                "rationale": storyboard.rationale,
                "provider": llm_settings.llm_provider,
                "model": effective_text_model(llm_settings),
                "revision_feedback": feedback,
                **({"creative_strategy": creative_strategy} if creative_strategy else {}),
            },
        )

    async def stream_rewrite_storyboard_text(
        self,
        session: AsyncSession,
        payload: VideoStoryboardRewriteRequest,
    ) -> AsyncIterator[dict]:
        feedback = payload.feedback.strip()
        if not feedback:
            yield {"type": "error", "message": "请先填写脚本修改意见。"}
            return
        if not payload.storyboard and not (payload.storyboard_text or "").strip():
            yield {"type": "error", "message": "请先生成或填写视频脚本。"}
            return

        campaign = await get_required(session, Campaign, payload.campaign_id)
        assets = await self._load_source_assets(
            session=session,
            campaign_id=payload.campaign_id,
            asset_ids=payload.creative_asset_ids,
        )
        draft = await self._load_draft(session, payload.draft_id, assets)
        context = await self._video_context(session, campaign, draft, assets, payload.metadata_json)
        yield {
            "type": "start",
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
        }

        full_text = ""
        try:
            llm, _llm_settings = self._llm_for_model(payload.model_id)
            async with llm_text_rate_limiter():
                async for event in _stream_text_with_heartbeat(
                    llm.stream_video_storyboard_revision_text(
                        campaign=campaign,  # type: ignore[arg-type]
                        draft=draft,  # type: ignore[arg-type]
                        assets=assets,  # type: ignore[arg-type]
                        duration_seconds=payload.duration_seconds,
                        aspect_ratio=payload.aspect_ratio,
                        context=context,
                        current_storyboard=payload.storyboard,
                        current_storyboard_text=payload.storyboard_text,
                        feedback=feedback,
                    ),
                    stage="video_storyboard_revision",
                ):
                    if event["type"] == "delta":
                        full_text += event["text"]
                    yield event
        except Exception as exc:
            yield {"type": "error", "message": f"视频脚本改写中断：{exc}"}
            return

        full_text = _ensure_storyboard_source_asset_notes(full_text, assets)
        yield {
            "type": "done",
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
            "text": full_text,
        }

    async def list_videos(
        self,
        session: AsyncSession,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> list[VideoAsset]:
        result = await session.execute(
            select(VideoAsset)
            .where(VideoAsset.campaign_id == campaign_id)
            .order_by(VideoAsset.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        videos = list(result.scalars().all())
        urls_changed = False
        for video in videos:
            urls_changed = self._normalize_local_video_url(video) or urls_changed
        if urls_changed:
            await session.commit()
        return videos

    async def start_video_generation(
        self,
        session: AsyncSession,
        video_id: str,
    ) -> VideoAsset:
        video = await get_required(session, VideoAsset, video_id)
        if video.provider_job_id and video.status in {
            VideoStatus.GENERATING.value,
            VideoStatus.GENERATED.value,
            VideoStatus.APPROVED.value,
        }:
            raise AppError("Video generation already started. Refresh status instead.")
        if video.status == VideoStatus.REJECTED.value:
            raise AppError("Rejected videos cannot be generated from this task.")

        provider_request = await self._build_provider_request(session, video)  # type: ignore[arg-type]
        started = await self.video_provider.start_generation(provider_request)
        video.provider_job_id = started.provider_job_id  # type: ignore[attr-defined]
        video.status = _provider_status_to_video_status(started.provider_status)
        video.error_message = None
        video.metadata_json = _merge_metadata(
            video.metadata_json,
            {
                "implementation_status": "provider_started",
                "video_provider": self.settings.video_provider,
                "provider_status": started.provider_status,
                "provider_request": _redact_provider_request_payload(started.request_payload),
                "provider_start_response": started.raw_response,
                "transient_url_note": (
                    "Provider video URLs may expire. Transfer to object storage before publishing."
                ),
            },
        )
        await session.commit()
        await session.refresh(video)
        return video  # type: ignore[return-value]

    async def refresh_video_generation(
        self,
        session: AsyncSession,
        video_id: str,
    ) -> VideoAsset:
        video = await get_required(session, VideoAsset, video_id)
        if not video.provider_job_id:
            raise AppError("Video generation has not been started for this task.")

        provider_status = await self.video_provider.get_generation_status(video.provider_job_id)  # type: ignore[attr-defined]
        video.status = _provider_status_to_video_status(provider_status.provider_status)
        provider_video_url = provider_status.video_url
        stored_video_url = None
        stored_storage_key = None
        implementation_status = "provider_status_synced"
        video_transfer_status = None
        storage_note = "Provider status was synced without scheduling storage transfer."
        should_schedule_transfer = False
        if provider_status.video_url and provider_status.provider_status == "succeeded":
            local_storage_key = self.video_storage.storage_key_for_public_url(
                provider_status.video_url
            )
            if local_storage_key:
                stored_storage_key = local_storage_key
                stored_video_url = self.video_storage.public_url_for_storage_key(
                    local_storage_key
                )
                video.url = stored_video_url
                video.storage_key = stored_storage_key
                implementation_status = "transferred"
                video_transfer_status = "completed"
                storage_note = "Provider video is already available in configured storage."
            else:
                should_schedule_transfer = True
                video.url = None
                video.storage_key = None
                implementation_status = "pending_transfer"
                video_transfer_status = "pending"
                storage_note = "Provider video transfer is scheduled in the video background queue."
        elif provider_status.video_url:
            video.url = provider_status.video_url
        self._normalize_local_video_url(video)
        video.error_message = provider_status.error_message
        video.metadata_json = _merge_metadata(
            video.metadata_json,
            {
                "implementation_status": implementation_status,
                "video_provider": self.settings.video_provider,
                "provider_status": provider_status.provider_status,
                "provider_status_response": provider_status.raw_response,
                "provider_video_url": provider_video_url,
                "stored_video_url": stored_video_url,
                "stored_storage_key": stored_storage_key,
                "last_frame_url": provider_status.last_frame_url,
                "video_transfer_status": video_transfer_status,
                "storage_note": storage_note,
            },
        )
        await session.commit()
        await session.refresh(video)
        if should_schedule_transfer:
            transfer_task = await self._schedule_video_transfer_task(session, video)
            video.metadata_json = _merge_metadata(
                video.metadata_json,
                {"video_transfer_task_id": transfer_task.id},
            )
            await session.commit()
            await session.refresh(video)
        return video  # type: ignore[return-value]

    async def transfer_completed_video(
        self,
        session: AsyncSession,
        video_id: str,
    ) -> VideoAsset:
        video = await get_required(session, VideoAsset, video_id)
        stored_video_url = self.video_storage.public_url_for_storage_key(video.storage_key)
        if stored_video_url:
            video.url = stored_video_url
            await self._apply_final_text_overlay_locks(video)
            video.metadata_json = _merge_metadata(
                video.metadata_json,
                {
                    "implementation_status": "transferred",
                    "video_transfer_status": "completed",
                    "stored_video_url": stored_video_url,
                    "stored_storage_key": video.storage_key,
                    "storage_note": "Provider video is already stored locally.",
                },
            )
            await session.commit()
            await session.refresh(video)
            return video  # type: ignore[return-value]

        metadata = video.metadata_json or {}
        provider_video_url = metadata.get("provider_video_url")
        if not isinstance(provider_video_url, str) or not provider_video_url.strip():
            provider_video_url = video.url
        if not isinstance(provider_video_url, str) or not provider_video_url.strip():
            raise AppError("Provider video URL is missing for transfer.")

        local_storage_key = self.video_storage.storage_key_for_public_url(provider_video_url)
        if local_storage_key:
            stored_storage_key = local_storage_key
            stored_video_url = self.video_storage.public_url_for_storage_key(local_storage_key)
        else:
            stored_video_url, stored_storage_key = await self.video_storage.transfer_provider_video(
                source_url=provider_video_url,
                video_id=video.id,
                provider_job_id=video.provider_job_id or video.id,
            )

        video.url = stored_video_url
        video.storage_key = stored_storage_key
        video.status = VideoStatus.GENERATED.value
        video.error_message = None
        await self._apply_final_text_overlay_locks(video)
        video.metadata_json = _merge_metadata(
            video.metadata_json,
            {
                "implementation_status": "transferred",
                "video_transfer_status": "completed",
                "provider_video_url": provider_video_url,
                "stored_video_url": stored_video_url,
                "stored_storage_key": stored_storage_key,
                "storage_note": "Provider video was transferred by the video background queue.",
            },
        )
        await session.commit()
        await session.refresh(video)
        return video  # type: ignore[return-value]

    async def _apply_final_text_overlay_locks(self, video: VideoAsset) -> None:
        metadata = video.metadata_json or {}
        overlays = metadata.get("final_text_overlay_locks")
        if not isinstance(overlays, list) or not overlays:
            return
        if metadata.get("final_text_overlay_status") == "applied":
            return
        if not all(isinstance(overlay, dict) for overlay in overlays):
            raise ProviderError("Final text overlay locks have an invalid stored format.")

        video_path = self.video_storage.path_for_storage_key(video.storage_key)
        if video_path is None:
            raise ProviderError("Saved video was not found for final overlay composition.")
        applied_overlays = await apply_final_text_overlay_locks(
            video_path,
            overlays=overlays,
        )
        video.metadata_json = _merge_metadata(
            video.metadata_json,
            {
                "final_text_overlay_status": "applied",
                "final_text_overlay_applied_locks": applied_overlays,
            },
        )


    async def _schedule_video_transfer_task(
        self,
        session: AsyncSession,
        video: VideoAsset,
    ):
        from backend.app.services.generation_task_dispatcher import schedule_generation_task
        from backend.app.services.generation_task_service import (
            VIDEO_QUEUE_NAME,
            GenerationTaskService,
        )

        metadata = video.metadata_json or {}
        provider_video_url = metadata.get("provider_video_url")
        task = await GenerationTaskService().create_task(
            session,
            queue_name=VIDEO_QUEUE_NAME,
            task_type="video_transfer",
            business_type="video_asset",
            business_id=video.id,
            campaign_id=video.campaign_id,
            payload={
                "video_id": video.id,
                "provider_job_id": video.provider_job_id,
                "provider_video_url": provider_video_url,
            },
            max_attempts=3,
            metadata={
                "source": "video_transfer",
                "video_id": video.id,
                "provider_job_id": video.provider_job_id,
                "provider_video_url": provider_video_url,
            },
        )
        schedule_generation_task(task)
        return task

    async def _load_source_assets(
        self,
        session: AsyncSession,
        campaign_id: str,
        asset_ids: list[str],
    ) -> list[CreativeAsset]:
        result = await session.execute(select(CreativeAsset).where(CreativeAsset.id.in_(asset_ids)))
        assets = list(result.scalars().all())
        found_ids = {asset.id for asset in assets}
        missing_ids = [asset_id for asset_id in asset_ids if asset_id not in found_ids]
        if missing_ids:
            raise AppError(f"Creative assets not found: {', '.join(missing_ids)}")

        assets_by_id = {asset.id: asset for asset in assets}
        wrong_campaign_ids = [asset.id for asset in assets if asset.campaign_id != campaign_id]
        if wrong_campaign_ids:
            raise AppError(
                "Creative assets do not belong to the requested campaign: "
                f"{', '.join(wrong_campaign_ids)}"
            )
        ordered_assets = [assets_by_id[asset_id] for asset_id in asset_ids]
        await repair_creative_asset_urls(session, ordered_assets, self.image_storage)
        return ordered_assets

    async def _load_draft(
        self,
        session: AsyncSession,
        draft_id: str | None,
        assets: list[CreativeAsset],
    ) -> CopyDraft | None:
        effective_draft_id = draft_id or (assets[0].draft_id if assets else None)
        if not effective_draft_id:
            raise AppError("Please select a copy draft before generating a video storyboard.")
        return await get_required(session, CopyDraft, effective_draft_id)  # type: ignore[return-value]

    async def _landing_page_context(
        self,
        session: AsyncSession,
        campaign_id: str,
    ) -> dict | None:
        latest_snapshot = await self.landing_pages.get_latest_snapshot(session, campaign_id)
        return snapshot_to_context(latest_snapshot) if latest_snapshot else None

    async def _video_context(
        self,
        session: AsyncSession,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list[CreativeAsset],
        metadata: dict | None,
    ) -> dict[str, Any]:
        landing_page_context = await self._landing_page_context(
            session,
            campaign_id=campaign.id,
        )
        creative_strategy = _creative_strategy_from_sources(
            campaign=campaign,
            draft=draft,
            assets=assets,
            metadata=metadata,
        )
        creative_strategy = merge_landing_visual_reference(
            creative_strategy,
            landing_page_context,
        )
        return {
            "work_order": (campaign.metadata_json or {}).get("work_order"),
            "landing_page": landing_page_context,
            **({"creative_strategy": creative_strategy} if creative_strategy else {}),
        }

    async def _build_provider_request(
        self,
        session: AsyncSession,
        video: VideoAsset,
    ) -> VideoGenerationRequest:
        if not video.source_asset_ids:
            raise AppError("Video task has no source images.")
        assets = await self._load_source_assets(
            session=session,
            campaign_id=video.campaign_id,
            asset_ids=video.source_asset_ids,
        )
        source_images: list[VideoSourceImage] = []
        missing_url_ids: list[str] = []
        for asset in assets:
            image_url = _resolve_video_source_image_url(asset, self.image_storage)
            if not image_url:
                missing_url_ids.append(asset.id)
                continue
            source_images.append(VideoSourceImage(id=asset.id, url=image_url))
        if missing_url_ids:
            raise AppError(
                "Selected creative assets do not have public image URLs: "
                f"{', '.join(missing_url_ids)}"
            )
        max_reference_images = self.settings.volcengine_video_max_reference_images
        too_many_reference_images = len(source_images) > max_reference_images
        if self.settings.video_provider == "volcengine" and too_many_reference_images:
            raise AppError(
                f"Seedance supports at most {max_reference_images} first/last frame images. "
                "Please create a video task with fewer selected images."
            )

        duration_seconds = (
            video.duration_seconds or self.settings.volcengine_video_max_duration_seconds
        )
        min_duration = self.settings.volcengine_video_min_duration_seconds
        max_duration = self.settings.volcengine_video_max_duration_seconds
        duration_supported = min_duration <= duration_seconds <= max_duration
        if self.settings.video_provider == "volcengine" and not duration_supported:
            raise AppError(
                f"Seedance supports {min_duration}-{max_duration} seconds. "
                "Please create a supported duration task."
            )

        creative_strategy = _strategy_from_metadata(video.metadata_json)
        prompt = (
            _prompt_with_creative_strategy(video.prompt, creative_strategy)
            or _storyboard_to_prompt(
                video.storyboard or [],
                creative_strategy=creative_strategy,
            )
        ).strip()
        return VideoGenerationRequest(
            prompt=prompt,
            source_images=source_images,
            duration_seconds=duration_seconds,
            aspect_ratio=video.aspect_ratio,
            metadata={
                "campaign_id": video.campaign_id,
                "draft_id": video.draft_id,
                "video_id": video.id,
                **({"creative_strategy": creative_strategy} if creative_strategy else {}),
            },
        )

    def _normalize_local_video_url(self, video: VideoAsset) -> bool:
        public_url = self.video_storage.public_url_for_storage_key(video.storage_key)
        if not public_url or video.url == public_url:
            return False
        video.url = public_url
        return True


def _storyboard_to_prompt(
    storyboard: list[dict],
    creative_strategy: dict | None = None,
) -> str:
    lines = [
        "Create a short ad video using this approved storyboard:",
        creative_safety_prompt_block(),
        _keyframe_brand_aaa_video_rules(),
    ]
    strategy_block = _creative_strategy_prompt_block(creative_strategy)
    if strategy_block:
        lines.append(strategy_block)
    for scene in storyboard:
        lines.append(
            " | ".join(
                part
                for part in [
                    f"Scene {scene.get('scene_index')}",
                    f"{scene.get('start_second', '-')}-{scene.get('end_second', '-')}s",
                    f"Visual: {_creative_safe_prompt_value(scene.get('visual'))}",
                    f"Subtitle: {_creative_safe_prompt_value(scene.get('subtitle'))}",
                    f"Motion: {_creative_safe_prompt_value(scene.get('motion'))}",
                    f"Voiceover: {_creative_safe_prompt_value(scene.get('voiceover'))}",
                ]
                if part and not part.endswith("None")
            )
        )
    return "\n".join(lines)


def _prompt_with_creative_strategy(
    prompt: str | None,
    creative_strategy: dict | None,
) -> str | None:
    if not prompt:
        return None
    stripped_prompt = prompt.strip()
    strategy_block = _creative_strategy_prompt_block(creative_strategy)
    safety_block = creative_safety_prompt_block()
    has_safety_block = safety_block in stripped_prompt
    safe_prompt = (
        stripped_prompt
        if has_safety_block
        else sanitize_creative_safety_text(stripped_prompt)
    )
    timing_rules = _keyframe_brand_aaa_video_rules()
    has_timing_rules = "0-3s opening rule" in stripped_prompt
    if strategy_block and "creative_strategy:" not in stripped_prompt:
        parts = [safe_prompt]
        if not has_safety_block:
            parts.append(safety_block)
        if not has_timing_rules:
            parts.append(timing_rules)
        parts.append(strategy_block)
        return "\n\n".join(parts)
    if has_safety_block:
        if has_timing_rules:
            return safe_prompt
        return f"{safe_prompt}\n\n{timing_rules}"
    if has_timing_rules:
        return safe_prompt
    return f"{safe_prompt}\n\n{safety_block}\n\n{timing_rules}"


def _keyframe_brand_aaa_video_rules() -> str:
    return (
        "Keyframe brand and 3A timing rules:\n"
        "0-3s opening rule: strictly continue the first frame with visible brand logo "
        "or cleaned brand name, plus a country-market strong visual character such as "
        "epic hero, king, warrior, bird-god-style boss, giant serpent boss, or stone "
        "guardian boss.\n"
        "3-9s middle VFX rule: VFX must be the main visual action, not a small garnish. "
        "Create high-impact 3A game-ad spectacle using coin explosion effects, divine "
        "light descent, portal effects, jackpot-style feedback, boss defeat, Score, "
        "Points, Stars, or Power rolling-number effects, and slow-motion reward bursts. "
        "Do not reduce 3-9s to ordinary path-choice gameplay, walking, simple ground "
        "lights, or UI-like buttons. Keep the middle cinematic and intense; at most keep "
        "a small brand logo.\n"
        "9-12s ending rule: strictly resolve into the last frame with visible brand "
        "logo or cleaned brand name, Start, Play Now, or Explore CTA, and a clean "
        "reward-resolution final CTA frame. Do not show cash amounts, real-money claims, "
        "withdrawal UI, recharge UI, balance UI, or guaranteed winning language."
    )


def _creative_strategy_prompt_block(creative_strategy: dict | None) -> str:
    if not isinstance(creative_strategy, dict):
        return ""
    if creative_strategy.get("schema_version") == "creative_strategy.v2":
        return _creative_strategy_v2_prompt_block(creative_strategy)
    return _legacy_creative_strategy_prompt_block(creative_strategy)


def _legacy_creative_strategy_prompt_block(creative_strategy: dict) -> str:
    template_id = creative_strategy.get("template_id") or "unknown"
    if template_id == "gaja_brand":
        return ""
    first_frame = creative_strategy.get("first_frame")
    last_frame = creative_strategy.get("last_frame")
    motion_direction = creative_strategy.get("motion_direction")
    guardrails = creative_strategy.get("compliance_guardrails")
    landing_visual_reference = creative_strategy.get("landing_visual_reference")
    negative_style_cues = creative_strategy.get("negative_style_cues")
    video_recipe = creative_strategy.get("video_recipe")
    country_style_pack = creative_strategy.get("country_style_pack")
    visual_concepts = creative_strategy.get("visual_concepts")
    text_layout_rules = creative_strategy.get("text_layout_rules")
    first_three_seconds = creative_strategy.get("first_three_seconds")
    lines = [
        f"creative_strategy: {template_id}",
        "Use this as a 12-second first/last-frame workflow.",
        f"first-frame hook: {_strategy_frame_summary(first_frame)}",
        f"last-frame resolution: {_strategy_frame_summary(last_frame)}",
    ]
    if template_id == "mini_game_pool":
        lines.append(
            "Mini-game-pool rule: open with gameplay-led curiosity and close on a "
            "low-text metallic GAJA game hub CTA beat with no numeric suffix."
        )
        lines.append(
            "Opening brand rule: show the visible GAJA logo or GAJA wordmark in the "
            "first frame; do not show any numeric suffix or brand-number text."
        )
    elif template_id == "gaja_brand":
        lines.append(
            "Opening brand rule: show the visible GAJA logo or GAJA wordmark in the "
            "first frame; do not show any numeric suffix or brand-number text."
        )
        lines.append(
            "GAJA brand rule: build the video around challenge, retry, reward, and a "
            "Start or Play Now CTA."
        )
    country_block = _country_style_pack_summary(country_style_pack)
    if country_block:
        lines.append(country_block)
    concepts_block = _visual_concepts_summary(visual_concepts)
    if concepts_block:
        lines.append(concepts_block)
    layout_block = _text_layout_rules_summary(text_layout_rules)
    if layout_block:
        lines.append(layout_block)
    first_three_block = _first_three_seconds_summary(first_three_seconds)
    if first_three_block:
        lines.append(first_three_block)
    reference_block = _landing_visual_reference_summary(landing_visual_reference)
    if reference_block:
        lines.append(reference_block)
    if isinstance(video_recipe, dict):
        beats = video_recipe.get("beats")
        if isinstance(beats, list) and beats:
            safe_beats = _creative_safe_direction_list(beats[:4])
            if safe_beats:
                lines.append(f"12-second beats: {'; '.join(safe_beats)}")
    if isinstance(negative_style_cues, list) and negative_style_cues:
        safe_negative = _creative_safe_direction_list(negative_style_cues[:6])
        if safe_negative:
            lines.append(
                f"Avoid style cues: {'; '.join(safe_negative)}"
            )
    if isinstance(motion_direction, list) and motion_direction:
        safe_motion = _creative_safe_direction_list(motion_direction[:4])
        if safe_motion:
            lines.append(
                f"Motion direction: {'; '.join(safe_motion)}"
            )
    if isinstance(guardrails, list) and guardrails:
        safe_guardrails = _creative_safe_direction_list(guardrails[:4])
        if safe_guardrails:
            lines.append(
                f"Compliance guardrails: {'; '.join(safe_guardrails)}"
            )
    return "\n".join(lines)


def _creative_strategy_v2_prompt_block(creative_strategy: dict) -> str:
    vertical = _strategy_vertical(creative_strategy)
    market = creative_strategy.get("market_context")
    audience = creative_strategy.get("audience_lens")
    brand_profile = creative_strategy.get("brand_profile")
    brand_display = creative_strategy.get("brand_display")
    style_pack_id = creative_strategy.get("style_pack_id")
    style_pack = creative_strategy.get("style_pack")
    country_overlay = creative_strategy.get("country_overlay")
    boss_guidance = creative_strategy.get("boss_guidance")
    reference_signal_pack = creative_strategy.get("reference_signal_pack")
    creative_package = str(creative_strategy.get("creative_package") or "").strip()
    text_policy = creative_strategy.get("text_brand_timing_policy")
    middle_vfx_policy = creative_strategy.get("middle_vfx_policy")
    vfx_library = creative_strategy.get("vfx_library")
    boss_matrix = creative_strategy.get("boss_matrix")
    reveal_mechanism_pool = creative_strategy.get("reveal_mechanism_pool")
    reveal_diversity_rule = creative_strategy.get("reveal_diversity_rule")
    cta_pool = creative_strategy.get("cta_pool")
    video_guidance = creative_strategy.get("video_guidance")
    market_game_style_pack = creative_strategy.get("market_game_style_pack")
    topic_plan = creative_strategy.get("topic_angle_plan")
    guardrails = creative_strategy.get("compliance_guardrails")

    lines = [f"creative_strategy: creative_strategy.v2 {vertical}".strip()]
    lines.append("Use this as duration-adaptive video direction; fit beats to duration_seconds.")
    if creative_package:
        lines.append(f"Creative package: {creative_package}")
    if creative_package == "gambling_vfx_spectacle_package":
        lines.append(
            "Gambling reveal diversity: Do not default every gambling creative to a "
            "physical door, gate, portal, or vault; at most one variant may use a "
            "physical door/gate/portal/vault composition."
        )

    style_pack_block = _style_pack_summary(style_pack_id, style_pack)
    if style_pack_block:
        lines.append(style_pack_block)

    brand_profile_block = _brand_profile_summary(brand_profile)
    if brand_profile_block:
        lines.append(brand_profile_block)

    if isinstance(brand_display, dict):
        cleaned_brand = _creative_safe_prompt_text(str(brand_display.get("cleaned_brand") or ""))
        digit_policy = _creative_safe_prompt_text(str(brand_display.get("digit_policy") or ""))
        if cleaned_brand:
            lines.append(f"Visible brand: {cleaned_brand}")
        if digit_policy:
            lines.append(f"Brand digit policy: {digit_policy}")

    country_overlay_block = _country_overlay_summary(country_overlay)
    if country_overlay_block:
        lines.append(country_overlay_block)

    boss_guidance_block = _boss_guidance_summary(boss_guidance)
    if boss_guidance_block:
        lines.append(boss_guidance_block)

    reference_signal_block = _reference_signal_summary(reference_signal_pack)
    if reference_signal_block:
        lines.append(reference_signal_block)

    if isinstance(text_policy, dict):
        allowed = _creative_safe_prompt_list(
            _list_value(text_policy.get("text_allowed_windows"))[:3]
        )
        middle_window = _creative_safe_prompt_text(str(text_policy.get("middle_window") or ""))
        middle_rule = _creative_safe_prompt_text(str(text_policy.get("middle_text_rule") or ""))
        if allowed or middle_window or middle_rule:
            allowed_text = " and ".join(allowed) if allowed else "0-3s and 9-12s"
            lines.append(
                "Text timing: use visible text and brand lockups only in "
                f"{allowed_text}; {middle_window or '3-9s'} middle segment {middle_rule}."
            )

    if isinstance(middle_vfx_policy, dict):
        window = _creative_safe_prompt_text(str(middle_vfx_policy.get("window") or "3-9s"))
        count = _creative_safe_prompt_text(
            str(middle_vfx_policy.get("required_vfx_count") or "2-3")
        )
        source = _creative_safe_prompt_text(str(middle_vfx_policy.get("source") or "vfx_library"))
        rule = _creative_safe_prompt_text(str(middle_vfx_policy.get("rule") or ""))
        lines.append(
            f"Middle VFX policy: {window} must select {count} VFX library items "
            f"from {source}; {rule}"
        )

    safe_vfx = _creative_safe_prompt_list(_list_value(vfx_library)[:10])
    if safe_vfx:
        lines.append(f"VFX library: {', '.join(safe_vfx)}")

    boss_block = _boss_matrix_summary(boss_matrix)
    if boss_block:
        lines.append(boss_block)

    safe_reveals = _creative_safe_prompt_list(_list_value(reveal_mechanism_pool)[:10])
    if safe_reveals:
        lines.append(f"Reveal mechanisms: {', '.join(safe_reveals)}")

    if isinstance(reveal_diversity_rule, str) and reveal_diversity_rule.strip():
        safe_reveal_rule = _creative_safe_prompt_text(reveal_diversity_rule)
        if safe_reveal_rule:
            lines.append(f"Reveal diversity rule: {safe_reveal_rule}")

    safe_ctas = _creative_safe_prompt_list(_list_value(cta_pool)[:6])
    if safe_ctas:
        lines.append(f"CTA pool: {', '.join(safe_ctas)}")

    if isinstance(market, dict):
        country = _creative_safe_prompt_text(
            str(market.get("country_code") or market.get("country") or "")
        )
        language = _creative_safe_prompt_text(str(market.get("language") or ""))
        if country or language:
            lines.append(f"Market context: {country} {language}".strip())

    if isinstance(audience, dict):
        age_range = _creative_safe_prompt_text(str(audience.get("age_range") or ""))
        style_items = audience.get("expression_style")
        safe_style = (
            _creative_safe_prompt_list(style_items[:4]) if isinstance(style_items, list) else []
        )
        audience_parts = [
            part
            for part in [f"age range {age_range}" if age_range else "", ", ".join(safe_style)]
            if part
        ]
        if audience_parts:
            lines.append(f"Audience lens for internal strategy only: {'; '.join(audience_parts)}")

    if isinstance(topic_plan, list) and topic_plan:
        safe_angles = []
        for item in topic_plan[:3]:
            if isinstance(item, dict):
                angle_type = _creative_safe_prompt_text(str(item.get("angle_type") or ""))
                purpose = _creative_safe_prompt_text(str(item.get("purpose") or ""))
                if angle_type:
                    safe_angles.append(f"{angle_type}: {purpose}".strip())
        if safe_angles:
            lines.append(f"Topic angles: {'; '.join(safe_angles)}")

    if isinstance(video_guidance, dict):
        narrative_beats = []
        for key, label in (
            ("opening", "Opening"),
            ("middle", "Middle"),
            ("ending", "Ending"),
        ):
            value = _creative_safe_prompt_text(str(video_guidance.get(key) or ""))
            if value:
                narrative_beats.append(f"{label}: {value}")
        if narrative_beats:
            lines.append(f"Duration-adaptive beats: {'; '.join(narrative_beats)}")

        for key, label in (
            ("short_video_rules", "Short duration rules"),
            ("medium_video_rules", "Medium duration rules"),
            ("long_video_rules", "Long duration rules"),
            ("beats_by_vertical", "Vertical beats"),
        ):
            values = video_guidance.get(key)
            if isinstance(values, list) and values:
                safe_values = _creative_safe_prompt_list(values[:5])
                if safe_values:
                    lines.append(f"{label}: {'; '.join(safe_values)}")

    game_style_block = _market_game_style_pack_summary(market_game_style_pack)
    if game_style_block:
        lines.append(game_style_block)

    if isinstance(guardrails, list) and guardrails:
        safe_guardrails = _creative_safe_prompt_list(guardrails[:6])
        if safe_guardrails:
            lines.append(f"Compliance guardrails: {'; '.join(safe_guardrails)}")
    return "\n".join(lines)


def _strategy_vertical(creative_strategy: dict) -> str:
    vertical = str(creative_strategy.get("vertical") or "").strip().casefold()
    if vertical == "gambling":
        return "gambling"
    if vertical == "game":
        return "game"
    return "ecommerce"


def _boss_matrix_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    for key, bosses in list(value.items())[:4]:
        angle = _creative_safe_prompt_text(str(key))
        safe_bosses = _creative_safe_prompt_list(_list_value(bosses)[:6])
        if angle and safe_bosses:
            parts.append(f"{angle}: {', '.join(safe_bosses)}")
    return f"Boss matrix: {' | '.join(parts)}" if parts else ""


def _style_pack_summary(style_pack_id: Any, style_pack: Any) -> str:
    safe_id = _creative_safe_prompt_text(str(style_pack_id or ""))
    parts = [f"Style pack: {safe_id}" if safe_id else ""]
    if isinstance(style_pack, dict):
        base = _creative_safe_prompt_text(str(style_pack.get("base_pack_id") or ""))
        overlay = _creative_safe_prompt_text(str(style_pack.get("country_overlay_id") or ""))
        composition = _creative_safe_prompt_text(str(style_pack.get("composition") or ""))
        if base:
            parts.append(f"base {base}")
        if overlay:
            parts.append(f"country overlay {overlay}")
        if composition:
            parts.append(f"composition {composition}")
    return "; ".join(part for part in parts if part)


def _brand_profile_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    visible = _creative_safe_prompt_text(str(value.get("visible_name") or ""))
    raw = _creative_safe_prompt_text(str(value.get("raw_name") or ""))
    source = _creative_safe_prompt_text(str(value.get("source_field") or ""))
    digit_policy = _creative_safe_prompt_text(str(value.get("digit_policy") or ""))
    if not visible and not raw:
        return ""
    parts = [f"visible {visible}" if visible else "", f"raw {raw}" if raw else ""]
    if source:
        parts.append(f"source {source}")
    if digit_policy:
        parts.append(f"digit policy {digit_policy}")
    return f"Brand profile: {'; '.join(part for part in parts if part)}"


def _country_overlay_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    overlay_id = _creative_safe_prompt_text(str(value.get("overlay_id") or ""))
    country_code = _creative_safe_prompt_text(str(value.get("country_code") or ""))
    parts = [f"Country overlay: {overlay_id} {country_code}".strip()]
    preferred_bosses = _creative_safe_prompt_list(
        _list_value(value.get("preferred_boss_groups"))[:4]
    )
    preferred_scenes = _creative_safe_prompt_list(_list_value(value.get("preferred_scenes"))[:5])
    preferred_reveals = _creative_safe_prompt_list(
        _list_value(value.get("preferred_reveal_mechanisms"))[:5]
    )
    visual_bias = _creative_safe_prompt_list(_list_value(value.get("visual_bias"))[:5])
    avoid = _creative_safe_prompt_list(_list_value(value.get("avoid"))[:5])
    if preferred_bosses:
        parts.append(f"preferred boss groups {', '.join(preferred_bosses)}")
    if preferred_scenes:
        parts.append(f"preferred scenes {', '.join(preferred_scenes)}")
    if preferred_reveals:
        parts.append(f"preferred reveals {', '.join(preferred_reveals)}")
    if visual_bias:
        parts.append(f"visual bias {', '.join(visual_bias)}")
    if avoid:
        parts.append(f"avoid {'; '.join(avoid)}")
    return " | ".join(part for part in parts if part.strip())


def _boss_guidance_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    role = _creative_safe_prompt_text(str(value.get("role") or ""))
    parts = [f"Boss guidance: {role}" if role else ""]
    must_show = _creative_safe_prompt_list(_list_value(value.get("must_show"))[:5])
    must_avoid = _creative_safe_prompt_list(_list_value(value.get("must_avoid"))[:5])
    if must_show:
        parts.append(f"must show {', '.join(must_show)}")
    if must_avoid:
        parts.append(f"must avoid {', '.join(must_avoid)}")
    return "; ".join(part for part in parts if part)


def _reference_signal_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    source = _creative_safe_prompt_text(str(value.get("source") or ""))
    parts = [f"Reference signal: {source}" if source else "Reference signal"]
    for key, label in (
        ("rhythm_bias", "rhythm"),
        ("visual_bias", "visual"),
        ("copy_bias", "copy"),
        ("boss_bias", "boss"),
        ("avoid", "avoid"),
    ):
        safe_values = _creative_safe_prompt_list(_list_value(value.get(key))[:5])
        if safe_values:
            parts.append(f"{label} {', '.join(safe_values)}")
    return " | ".join(part for part in parts if part.strip())


def _country_style_pack_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    code = _creative_safe_prompt_text(str(value.get("country_code") or ""))
    label = _creative_safe_prompt_text(str(value.get("country_label") or ""))
    family = _creative_safe_prompt_text(str(value.get("style_family") or ""))
    style_cues = value.get("style_cues")
    guardrails = value.get("cultural_safety_guardrails")
    parts = [f"Country style pack: {code} {label}".strip()]
    if family:
        parts.append(f"style family: {family}")
    if isinstance(style_cues, list) and style_cues:
        safe_cues = _creative_safe_prompt_list(style_cues[:5])
        if safe_cues:
            parts.append(f"style cues: {', '.join(safe_cues)}")
    if isinstance(guardrails, list) and guardrails:
        safe_guardrails = _creative_safe_prompt_list(guardrails[:3])
        if safe_guardrails:
            parts.append(f"cultural guardrails: {'; '.join(safe_guardrails)}")
    return " | ".join(part for part in parts if part.strip())


def _market_game_style_pack_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    code = _creative_safe_prompt_text(str(value.get("country_code") or ""))
    label = _creative_safe_prompt_text(str(value.get("country_label") or ""))
    parts = [f"Market game style pack: {code} {label}".strip()]

    interests = _creative_safe_prompt_list(_list_value(value.get("game_interest_hypothesis"))[:5])
    if interests:
        parts.append(f"Game interest hypothesis: {', '.join(interests)}")

    archetypes: list[str] = []
    visual_language: list[str] = []
    aaa = value.get("aaa_game_inspiration")
    if isinstance(aaa, dict):
        archetypes = _creative_safe_prompt_list(_list_value(aaa.get("genre_archetypes"))[:4])
        visual_language = _creative_safe_prompt_list(_list_value(aaa.get("visual_language"))[:4])
    if archetypes or visual_language:
        pieces = []
        if archetypes:
            pieces.append(f"genre archetypes {', '.join(archetypes)}")
        if visual_language:
            pieces.append(f"visual language {', '.join(visual_language)}")
        parts.append(f"AAA-style inspiration: {'; '.join(pieces)}")

    visual_world = _creative_safe_prompt_list(_list_value(value.get("visual_world"))[:5])
    if visual_world:
        parts.append(f"Visual world: {', '.join(visual_world)}")

    gameplay = value.get("gameplay_process")
    gameplay_block = _gameplay_process_summary(gameplay)
    if gameplay_block:
        parts.append(gameplay_block)

    cultural = value.get("cultural_safety")
    cultural_block = _cultural_safety_summary(cultural)
    if cultural_block:
        parts.append(cultural_block)

    return "\n".join(part for part in parts if part.strip())


def _gameplay_process_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    pieces: list[str] = []
    for key, label in (
        ("player_goal", "player goal"),
        ("opening_conflict", "opening conflict"),
        ("ending_transition", "ending transition"),
    ):
        text = _creative_safe_prompt_text(str(value.get(key) or ""))
        if text:
            pieces.append(f"{label}: {text}")

    actions = _creative_safe_prompt_list(_list_value(value.get("player_actions"))[:5])
    if actions:
        pieces.append(f"player actions: {', '.join(actions)}")

    feedback = _creative_safe_prompt_list(_list_value(value.get("progression_feedback"))[:5])
    if feedback:
        pieces.append(f"progression feedback: {', '.join(feedback)}")

    if not pieces:
        return ""
    return (
        "Gameplay process: show the player actively playing the game; "
        + "; ".join(pieces)
    )


def _cultural_safety_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    allowed = _creative_safe_prompt_list(_list_value(value.get("allowed"))[:4])
    avoid = _creative_safe_prompt_list(_list_value(value.get("avoid"))[:5])
    pieces = []
    if allowed:
        pieces.append(f"allowed {', '.join(allowed)}")
    if avoid:
        pieces.append(f"avoid {', '.join(avoid)}")
    return f"Cultural safety: {'; '.join(pieces)}" if pieces else ""


def _visual_concepts_summary(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    concept_parts: list[str] = []
    for concept in value[:3]:
        if not isinstance(concept, dict):
            continue
        concept_id = _creative_safe_prompt_text(str(concept.get("concept_id") or ""))
        name = _creative_safe_prompt_text(str(concept.get("name") or ""))
        theme = _creative_safe_prompt_text(str(concept.get("visual_theme") or ""))
        first = _creative_safe_prompt_text(str(concept.get("first_frame_visual") or ""))
        last = _creative_safe_prompt_text(str(concept.get("last_frame_visual") or ""))
        motion = _creative_safe_prompt_text(str(concept.get("motion_hint") or ""))
        pieces = [
            f"{concept.get('variant_index')}: {concept_id}",
            name,
            f"theme {theme}" if theme else "",
            f"first {first}" if first else "",
            f"last {last}" if last else "",
            f"motion {motion}" if motion else "",
        ]
        concept_text = "; ".join(piece for piece in pieces if str(piece).strip())
        if concept_text:
            concept_parts.append(concept_text)
    if not concept_parts:
        return ""
    return f"Variant visual concepts: {' || '.join(concept_parts)}"


def _text_layout_rules_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    safe_area_width = value.get("safe_area_width_pct")
    top_bottom_margin = value.get("top_bottom_margin_pct")
    max_layers = value.get("max_visible_text_layers")
    allowed_visible_text = value.get("allowed_visible_text")
    allowed_text = _creative_safe_prompt_list(
        allowed_visible_text if isinstance(allowed_visible_text, list) else []
    )
    instruction = _creative_safe_prompt_text(str(value.get("layout_instruction") or ""))
    parts = [
        f"max {max_layers} visible text layers" if max_layers else "",
        f"safe area width {safe_area_width}%" if safe_area_width else "",
        f"{top_bottom_margin}% top/bottom margins" if top_bottom_margin else "",
        "auto-fit text" if value.get("auto_fit") is True else "",
        "no overflow outside the image or video frame",
    ]
    if allowed_text:
        parts.append(f"allowed text examples: {', '.join(allowed_text[:3])}")
    if instruction:
        parts.append(instruction)
    return f"Text layout rules: {'; '.join(part for part in parts if part)}"


def _first_three_seconds_summary(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    beats: list[str] = []
    for item in value[:3]:
        if not isinstance(item, dict):
            continue
        time_range = _creative_safe_prompt_text(str(item.get("time_range") or ""))
        beat = _creative_safe_prompt_text(str(item.get("beat") or ""))
        visible_text = _creative_safe_prompt_text(str(item.get("visible_text") or ""))
        if not time_range or not beat:
            continue
        suffix = f" visible text {visible_text}" if visible_text else ""
        beats.append(f"{time_range}: {beat}{suffix}")
    if not beats:
        return ""
    return f"First 3 seconds hook: {'; '.join(beats)}"


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _strategy_frame_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return "follow the approved frame role."
    role = value.get("role")
    visual_must_include = value.get("visual_must_include")
    parts = []
    if role:
        safe_role = _creative_safe_prompt_text(str(role))
        if safe_role:
            parts.append(safe_role)
    if isinstance(visual_must_include, list):
        parts.extend(_creative_safe_direction_list(visual_must_include[:5]))
    composition = value.get("composition")
    if composition:
        safe_composition = _creative_safe_direction_text(str(composition))
        if safe_composition:
            parts.append(safe_composition)
    return "; ".join(parts) if parts else "follow the approved frame role."


def _landing_visual_reference_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = ["Landing visual reference:"]
    for key, label in (
        ("palette", "palette"),
        ("surface_style", "surface style"),
        ("gameplay_moment_archetypes", "gameplay moments"),
        ("composition_cues", "composition cues"),
    ):
        item = value.get(key)
        if isinstance(item, list) and item:
            safe_items = _creative_safe_direction_list(item[:4])
            if not safe_items:
                continue
            parts.append(
                f"{label}: {', '.join(safe_items)}"
            )
    video_recipe = value.get("video_recipe")
    if isinstance(video_recipe, dict):
        beats = video_recipe.get("beats")
        if isinstance(beats, list) and beats:
            safe_beats = _creative_safe_direction_list(beats[:4])
            if safe_beats:
                parts.append(f"video beats: {'; '.join(safe_beats)}")
    return " | ".join(parts) if len(parts) > 1 else ""


def _creative_safe_prompt_text(value: str) -> str:
    text = sanitize_creative_safety_text(str(value).strip())
    replacements = {
        "title treatment": "title styling",
        "Title treatment": "Title styling",
        "treatment": "styling",
        "Treatment": "Styling",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


def _creative_safe_prompt_value(value: Any) -> str:
    if value is None:
        return ""
    return sanitize_creative_safety_text(str(value))


def _creative_safe_prompt_list(values: list[Any]) -> list[str]:
    return [
        safe_item
        for item in values
        if str(item).strip()
        for safe_item in [_creative_safe_prompt_text(str(item))]
        if safe_item
    ]


def _creative_safe_direction_text(value: str) -> str:
    safe_text = _creative_safe_prompt_text(value)
    if not safe_text or _contains_fixed_game_card_style(safe_text):
        return ""
    return safe_text


def _creative_safe_direction_list(values: list[Any]) -> list[str]:
    return [
        safe_item
        for item in values
        if str(item).strip()
        for safe_item in [_creative_safe_direction_text(str(item))]
        if safe_item
    ]


def _contains_fixed_game_card_style(value: str) -> bool:
    text = str(value).casefold()
    return any(term in text for term in FIXED_GAME_CARD_STYLE_TERMS)


def _strategy_from_context(context: dict[str, Any]) -> dict | None:
    return _strategy_from_metadata(context)


def _creative_strategy_from_sources(
    campaign: Campaign,
    draft: CopyDraft | None,
    assets: list[CreativeAsset],
    metadata: dict | None = None,
) -> dict | None:
    for source in (
        metadata,
        campaign.metadata_json if isinstance(campaign.metadata_json, dict) else None,
        draft.metadata_json if draft and isinstance(draft.metadata_json, dict) else None,
        *(
            asset.metadata_json if isinstance(asset.metadata_json, dict) else None
            for asset in assets
        ),
    ):
        strategy = _strategy_from_metadata(source)
        if strategy:
            return strategy
    return None


def _strategy_from_metadata(metadata: Any) -> dict | None:
    if not isinstance(metadata, dict):
        return None
    strategy = metadata.get("creative_strategy")
    return strategy if isinstance(strategy, dict) else None


def _ensure_storyboard_source_asset_notes(text: str, assets: list[CreativeAsset]) -> str:
    asset_ids = [asset.id for asset in assets if asset.id]
    if not asset_ids:
        return text
    empty_reference_markers = (
        "No source image provided",
        "No reference image required",
    )
    if not any(marker in text for marker in empty_reference_markers):
        return text
    source_note = ", ".join(asset_ids)
    return text.replace(
        "Source image id notes: No source image provided.",
        f"Source image id notes: {source_note}.",
    ).replace(
        "Source image id notes: No source image provided",
        f"Source image id notes: {source_note}",
    ).replace(
        "Source image id notes: No reference image required.",
        f"Source image id notes: {source_note}.",
    ).replace(
        "Source image id notes: No reference image required",
        f"Source image id notes: {source_note}",
    )


async def _stream_text_with_heartbeat(
    chunks: AsyncIterator[str],
    stage: str,
) -> AsyncIterator[dict]:
    chunk_task = asyncio.create_task(anext(chunks))
    try:
        while True:
            done, _ = await asyncio.wait(
                {chunk_task},
                timeout=VIDEO_STREAM_HEARTBEAT_SECONDS,
            )
            if not done:
                yield _heartbeat_event(stage)
                continue

            try:
                chunk = chunk_task.result()
            except StopAsyncIteration:
                break

            if chunk:
                yield {"type": "delta", "text": chunk}
            chunk_task = asyncio.create_task(anext(chunks))
    finally:
        if not chunk_task.done():
            chunk_task.cancel()
            try:
                await chunk_task
            except asyncio.CancelledError:
                pass
        aclose = getattr(chunks, "aclose", None)
        if aclose:
            await aclose()


def _validate_video_keyframe_source_assets(assets: list[CreativeAsset]) -> None:
    keyframe_assets = [asset for asset in assets if _is_video_keyframe_asset(asset)]
    if not keyframe_assets:
        return

    if len(keyframe_assets) != len(assets):
        raise AppError(
            "Selected video keyframe sources must use one complete approved keyframe scheme."
        )

    groups = {_positive_metadata_int(asset, "keyframe_group") for asset in keyframe_assets}
    groups.discard(0)
    if len(groups) != 1:
        raise AppError(
            "Selected video keyframe sources must use one complete approved keyframe scheme."
        )

    if any(asset.status != CreativeStatus.APPROVED.value for asset in keyframe_assets):
        raise AppError(
            "Selected video keyframe scheme has regenerated images that still need approval. "
            "Use one complete approved keyframe scheme before video generation."
        )

    group_size = max(
        1,
        _positive_metadata_int(keyframe_assets[0], "keyframe_group_size") or 2,
    )
    positions = {
        _positive_metadata_int(asset, "keyframe_position") for asset in keyframe_assets
    }
    required_positions = set(range(1, group_size + 1))
    if not required_positions.issubset(positions):
        raise AppError(
            "Selected video keyframe scheme is incomplete. "
            "Use one complete approved keyframe scheme before video generation."
        )


def _is_video_keyframe_asset(asset: CreativeAsset) -> bool:
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    return metadata.get("generation_mode") == "video_keyframe_variants"


def _positive_metadata_int(asset: CreativeAsset, key: str) -> int:
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    value = metadata.get(key)
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _heartbeat_event(stage: str) -> dict:
    return {
        "type": "heartbeat",
        "stage": stage,
        "interval_seconds": VIDEO_STREAM_HEARTBEAT_SECONDS,
    }


def _resolve_video_source_image_url(
    asset: CreativeAsset,
    image_storage: ImageStorageService,
) -> str | None:
    if image_storage.settings.object_storage_provider == "local":
        storage_key = asset.storage_key or image_storage.storage_key_for_public_url(asset.url)
        data_url = image_storage.data_url_for_storage_key(storage_key)
        if data_url:
            return data_url

        provider_url = (asset.metadata_json or {}).get("provider_image_url")
        if isinstance(provider_url, str) and provider_url.startswith(("http://", "https://")):
            return provider_url
    return resolve_creative_image_url(asset, image_storage)


def _redact_provider_request_payload(payload: dict) -> dict:
    redacted = dict(payload)
    content = payload.get("content")
    if not isinstance(content, list):
        return redacted

    redacted_content = []
    for item in content:
        if not isinstance(item, dict):
            redacted_content.append(item)
            continue

        redacted_item = dict(item)
        image_url = redacted_item.get("image_url")
        if isinstance(image_url, dict):
            redacted_image_url = dict(image_url)
            url = redacted_image_url.get("url")
            if isinstance(url, str) and url.startswith("data:"):
                redacted_image_url["url"] = _redact_data_url(url)
            redacted_item["image_url"] = redacted_image_url
        redacted_content.append(redacted_item)

    redacted["content"] = redacted_content
    return redacted


def _redact_data_url(url: str) -> str:
    header, separator, encoded = url.partition(",")
    if not separator:
        return "data:<redacted>"

    padding = encoded.count("=")
    decoded_bytes = max((len(encoded) * 3 // 4) - padding, 0)
    return f"{header},<redacted {decoded_bytes} bytes>"


def _provider_status_to_video_status(provider_status: str) -> str:
    status = provider_status.lower()
    if status in {"queued", "running", "created", "pending"}:
        return VideoStatus.GENERATING.value
    if status == "succeeded":
        return VideoStatus.GENERATED.value
    if status in {"failed", "expired", "cancelled", "canceled"}:
        return VideoStatus.FAILED.value
    return VideoStatus.GENERATING.value


def _merge_metadata(current: dict | None, updates: dict) -> dict:
    cleaned_updates = {key: value for key, value in updates.items() if value is not None}
    return {**(current or {}), **cleaned_updates}
