import asyncio
from collections.abc import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.core.errors import AppError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import VideoStatus
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
from backend.app.services.brand_safety_policy import BRAND_SAFETY_VISUAL_BAN
from backend.app.services.creative_asset_urls import (
    repair_creative_asset_urls,
    resolve_creative_image_url,
)
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.landing_page_service import LandingPageService, snapshot_to_context
from backend.app.services.utils import get_required
from backend.app.services.video_storage_service import VideoStorageService

VIDEO_STREAM_HEARTBEAT_SECONDS = 5.0


class VideoService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = get_llm_provider(self.settings)
        self.video_provider = get_video_provider(self.settings)
        self.landing_pages = LandingPageService()
        self.image_storage = ImageStorageService(self.settings)
        self.video_storage = VideoStorageService(self.settings)

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
        context = {
            "work_order": campaign.metadata_json.get("work_order"),
            "landing_page": await self._landing_page_context(
                session,
                campaign_id=payload.campaign_id,
            ),
        }
        storyboard = await self.llm.generate_video_storyboard(
            campaign=campaign,  # type: ignore[arg-type]
            draft=draft,  # type: ignore[arg-type]
            assets=assets,  # type: ignore[arg-type]
            duration_seconds=payload.duration_seconds,
            aspect_ratio=payload.aspect_ratio,
            context=context,
            instructions=payload.instructions,
        )
        storyboard_items = [scene.model_dump() for scene in storyboard.scenes]
        prompt = _storyboard_to_prompt(storyboard_items)
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
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "instructions": payload.instructions,
            },
        )

    async def create_video_job(
        self,
        session: AsyncSession,
        payload: VideoGenerateRequest,
    ) -> VideoAsset:
        await get_required(session, Campaign, payload.campaign_id)
        if payload.draft_id:
            await get_required(session, CopyDraft, payload.draft_id)

        assets = await self._load_source_assets(
            session=session,
            campaign_id=payload.campaign_id,
            asset_ids=payload.creative_asset_ids,
        )
        inferred_draft_id = payload.draft_id or assets[0].draft_id
        landing_page_context = await self._landing_page_context(
            session,
            campaign_id=payload.campaign_id,
        )

        video = VideoAsset(
            campaign_id=payload.campaign_id,
            draft_id=inferred_draft_id,
            source_asset_ids=payload.creative_asset_ids,
            prompt=payload.prompt,
            storyboard=payload.storyboard,
            duration_seconds=payload.duration_seconds,
            aspect_ratio=payload.aspect_ratio,
            metadata_json={
                **payload.metadata_json,
                "implementation_status": "configured",
                "note": "Video generation task is configured. Call generate to start provider job.",
                "landing_page": landing_page_context,
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
        context = {
            "work_order": campaign.metadata_json.get("work_order"),
            "landing_page": await self._landing_page_context(
                session,
                campaign_id=payload.campaign_id,
            ),
        }
        yield {
            "type": "start",
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
        }

        full_text = ""
        try:
            async for event in _stream_text_with_heartbeat(
                self.llm.stream_video_storyboard_text(
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
        context = {
            "work_order": campaign.metadata_json.get("work_order"),
            "landing_page": await self._landing_page_context(
                session,
                campaign_id=payload.campaign_id,
            ),
        }
        storyboard = await self.llm.revise_video_storyboard(
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
        prompt = _storyboard_to_prompt(storyboard_items)
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
                "provider": self.settings.llm_provider,
                "model": self.settings.llm_model,
                "revision_feedback": feedback,
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
        context = {
            "work_order": campaign.metadata_json.get("work_order"),
            "landing_page": await self._landing_page_context(
                session,
                campaign_id=payload.campaign_id,
            ),
        }
        yield {
            "type": "start",
            "duration_seconds": payload.duration_seconds,
            "aspect_ratio": payload.aspect_ratio,
        }

        full_text = ""
        try:
            async for event in _stream_text_with_heartbeat(
                self.llm.stream_video_storyboard_revision_text(
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
        if provider_status.video_url and provider_status.provider_status == "succeeded":
            local_storage_key = self.video_storage.storage_key_for_public_url(
                provider_status.video_url
            )
            if local_storage_key:
                stored_storage_key = local_storage_key
                stored_video_url = self.video_storage.public_url_for_storage_key(
                    local_storage_key
                )
            else:
                stored_video_url, stored_storage_key = (
                    await self.video_storage.transfer_provider_video(
                        source_url=provider_status.video_url,
                        video_id=video.id,
                        provider_job_id=provider_status.provider_job_id,
                    )
                )
            video.url = stored_video_url
            video.storage_key = stored_storage_key
        elif provider_status.video_url:
            video.url = provider_status.video_url
        self._normalize_local_video_url(video)
        video.error_message = provider_status.error_message
        video.metadata_json = _merge_metadata(
            video.metadata_json,
            {
                "implementation_status": "provider_status_synced",
                "video_provider": self.settings.video_provider,
                "provider_status": provider_status.provider_status,
                "provider_status_response": provider_status.raw_response,
                "provider_video_url": provider_video_url,
                "stored_video_url": stored_video_url,
                "stored_storage_key": stored_storage_key,
                "last_frame_url": provider_status.last_frame_url,
                "storage_note": (
                    "Provider video was transferred to configured object storage when succeeded."
                ),
            },
        )
        await session.commit()
        await session.refresh(video)
        return video  # type: ignore[return-value]

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
        effective_draft_id = draft_id or assets[0].draft_id
        return await get_required(session, CopyDraft, effective_draft_id)  # type: ignore[return-value]

    async def _landing_page_context(
        self,
        session: AsyncSession,
        campaign_id: str,
    ) -> dict | None:
        latest_snapshot = await self.landing_pages.get_latest_snapshot(session, campaign_id)
        return snapshot_to_context(latest_snapshot) if latest_snapshot else None

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

        prompt = (video.prompt or _storyboard_to_prompt(video.storyboard or [])).strip()
        return VideoGenerationRequest(
            prompt=prompt,
            source_images=source_images,
            duration_seconds=duration_seconds,
            aspect_ratio=video.aspect_ratio,
            metadata={
                "campaign_id": video.campaign_id,
                "draft_id": video.draft_id,
                "video_id": video.id,
            },
        )

    def _normalize_local_video_url(self, video: VideoAsset) -> bool:
        public_url = self.video_storage.public_url_for_storage_key(video.storage_key)
        if not public_url or video.url == public_url:
            return False
        video.url = public_url
        return True


def _storyboard_to_prompt(storyboard: list[dict]) -> str:
    lines = [
        "Create a short ad video using this approved storyboard:",
        BRAND_SAFETY_VISUAL_BAN,
    ]
    for scene in storyboard:
        lines.append(
            " | ".join(
                part
                for part in [
                    f"Scene {scene.get('scene_index')}",
                    f"{scene.get('start_second', '-')}-{scene.get('end_second', '-')}s",
                    f"Visual: {scene.get('visual')}",
                    f"Subtitle: {scene.get('subtitle')}",
                    f"Motion: {scene.get('motion')}",
                    f"Voiceover: {scene.get('voiceover')}",
                ]
                if part and not part.endswith("None")
            )
        )
    return "\n".join(lines)


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
