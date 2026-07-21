import asyncio
import base64
import binascii
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppError, NotFoundError, ProviderError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import VideoStatus
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.schemas.external_video_generation import (
    ExternalVideoGenerationCreate,
    ExternalVideoGenerationJobRead,
)
from backend.app.services.external_sources import EXTERNAL_VIDEO_GENERATION_SOURCE
from backend.app.services.generation_task_service import VIDEO_QUEUE_NAME, GenerationTaskService
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.video_service import VideoService

VIDEO_START_RETRY_DELAYS_SECONDS = (0.2, 0.5)
EXTERNAL_VIDEO_FAILED_REQUEUE_LIMIT = 2
EXTERNAL_VIDEO_START_TASK_TYPE = "external_video_start"
LEGACY_EMPTY_VOLCENGINE_VIDEO_ERROR = "Volcengine video API request failed:"
LEGACY_EMPTY_VOLCENGINE_VIDEO_ERROR_MESSAGE = (
    "Volcengine video API request failed: request failed before response"
)


@dataclass(frozen=True)
class DecodedImage:
    data: bytes
    mime_type: str


class ExternalVideoGenerationService:
    def __init__(self) -> None:
        self.image_storage = ImageStorageService()
        self.video_service = VideoService()
        self.task_service = GenerationTaskService()

    async def create_video(
        self,
        session: AsyncSession,
        payload: ExternalVideoGenerationCreate,
    ) -> tuple[ExternalVideoGenerationJobRead, GenerationTask | None]:
        storyboard_text = payload.storyboard_text.strip()
        if not storyboard_text:
            raise AppError("storyboard_text is required")
        if len(payload.images) != 2:
            raise AppError("images must contain exactly 2 base64 images")

        existing = await self._find_existing_video(session, payload.external_request_id)
        if existing:
            retry_task = await self._requeue_failed_external_video_if_safe(
                session,
                existing,
                external_request_id=payload.external_request_id,
            )
            if retry_task is not None:
                await session.refresh(existing)
                return self._job_read(existing), retry_task
            return self._job_read(existing), None

        decoded_images = [_decode_base64_image(image) for image in payload.images]
        max_bytes = self.image_storage.settings.image_download_max_bytes
        too_large = [image for image in decoded_images if len(image.data) > max_bytes]
        if too_large:
            raise AppError("image exceeds configured size limit")

        try:
            campaign = Campaign(
                name="External video generation",
                work_order_id=None,
                metadata_json={
                    "source": EXTERNAL_VIDEO_GENERATION_SOURCE,
                    "external_request_id": payload.external_request_id,
                    "duration_seconds": payload.duration_seconds,
                    "aspect_ratio": payload.aspect_ratio,
                },
            )
            session.add(campaign)
            await session.flush()

            topic = ContentTopic(
                campaign_id=campaign.id,
                title="External video generation",
                angle=storyboard_text,
                source_data={
                    "source": EXTERNAL_VIDEO_GENERATION_SOURCE,
                    "external_request_id": payload.external_request_id,
                    "storyboard_text": storyboard_text,
                    "duration_seconds": payload.duration_seconds,
                    "aspect_ratio": payload.aspect_ratio,
                },
            )
            session.add(topic)
            await session.flush()

            draft = CopyDraft(
                campaign_id=campaign.id,
                topic_id=topic.id,
                body=storyboard_text,
                primary_text=storyboard_text,
                model_name="external-video-generation",
                prompt_version="external.video.v1",
                metadata_json={
                    "source": EXTERNAL_VIDEO_GENERATION_SOURCE,
                    "external_request_id": payload.external_request_id,
                    "storyboard_text": storyboard_text,
                },
            )
            session.add(draft)
            await session.flush()

            source_assets = self._build_source_assets(
                campaign=campaign,
                draft=draft,
                images=decoded_images,
                prompt=storyboard_text,
                aspect_ratio=payload.aspect_ratio,
                external_request_id=payload.external_request_id,
            )
            for asset in source_assets:
                session.add(asset)
            await session.flush()

            video = VideoAsset(
                campaign_id=campaign.id,
                draft_id=draft.id,
                source_asset_ids=[asset.id for asset in source_assets],
                prompt=storyboard_text,
                storyboard=[],
                duration_seconds=payload.duration_seconds,
                aspect_ratio=payload.aspect_ratio,
                status=VideoStatus.REQUESTED.value,
                metadata_json={
                    "source": EXTERNAL_VIDEO_GENERATION_SOURCE,
                    "external_request_id": payload.external_request_id,
                    "storyboard_text": storyboard_text,
                    "duration_seconds": payload.duration_seconds,
                    "aspect_ratio": payload.aspect_ratio,
                    "implementation_status": "configured",
                },
            )
            session.add(video)
            await session.flush()

            # create_task commits the session. The worker must see the video
            # and task before dispatch.
            task = await self.task_service.create_task(
                session,
                queue_name=VIDEO_QUEUE_NAME,
                task_type=EXTERNAL_VIDEO_START_TASK_TYPE,
                business_type=EXTERNAL_VIDEO_GENERATION_SOURCE,
                business_id=video.id,
                campaign_id=campaign.id,
                payload={"video_id": video.id},
                max_attempts=1,
                metadata={
                    "source": EXTERNAL_VIDEO_GENERATION_SOURCE,
                    "external_request_id": payload.external_request_id,
                },
            )
            await session.refresh(video)
            return self._job_read(video), task
        except Exception:
            await session.rollback()
            raise

    async def get_job(
        self,
        session: AsyncSession,
        job_id: str,
    ) -> ExternalVideoGenerationJobRead:
        video = await self._load_external_video(session, job_id)

        if video.status == VideoStatus.GENERATING.value and video.provider_job_id:
            video = await self.video_service.refresh_video_generation(session, video.id)

        return self._job_read(video)

    async def execute_start_task(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> VideoAsset:
        if task.task_type != EXTERNAL_VIDEO_START_TASK_TYPE:
            raise AppError(f"Unsupported external video task type: {task.task_type}")

        payload = task.payload_json or {}
        video_id = str(payload.get("video_id") or task.business_id or "").strip()
        if not video_id:
            raise AppError("video_id is required for external video start tasks.")

        video = await self._load_external_video(session, video_id)
        if video.provider_job_id and video.status in {
            VideoStatus.GENERATING.value,
            VideoStatus.GENERATED.value,
            VideoStatus.APPROVED.value,
        }:
            return video

        try:
            return await self._start_video_generation_with_retries(session, video)
        except Exception as exc:
            await self._mark_video_start_failed(session, video_id, exc)
            raise

    def _build_source_assets(
        self,
        *,
        campaign: Campaign,
        draft: CopyDraft,
        images: list[DecodedImage],
        prompt: str,
        aspect_ratio: str,
        external_request_id: str | None,
    ) -> list[CreativeAsset]:
        assets: list[CreativeAsset] = []
        roles = ["first_frame", "last_frame"]
        for index, image in enumerate(images):
            asset_id = str(uuid4())
            url, storage_key = self._store_source_image(
                image=image,
                campaign_id=campaign.id,
                asset_id=asset_id,
            )
            assets.append(
                CreativeAsset(
                    id=asset_id,
                    campaign_id=campaign.id,
                    draft_id=draft.id,
                    kind="image",
                    url=url,
                    storage_key=storage_key,
                    prompt=prompt,
                    alt_text=roles[index],
                    size=aspect_ratio,
                    metadata_json={
                        "source": EXTERNAL_VIDEO_GENERATION_SOURCE,
                        "external_request_id": external_request_id,
                        "material_type": "video_source_image",
                        "keyframe_role": roles[index],
                        "image_index": index + 1,
                        "mime_type": image.mime_type,
                    },
                )
            )
        return assets

    async def _find_existing_video(
        self,
        session: AsyncSession,
        external_request_id: str | None,
    ) -> VideoAsset | None:
        request_id = (external_request_id or "").strip()
        if not request_id:
            return None

        result = await session.execute(select(VideoAsset).order_by(VideoAsset.created_at.desc()))
        for video in result.scalars().all():
            metadata = video.metadata_json or {}
            if (
                metadata.get("source") == EXTERNAL_VIDEO_GENERATION_SOURCE
                and metadata.get("external_request_id") == request_id
            ):
                return video
        return None

    async def _load_external_video(self, session: AsyncSession, video_id: str) -> VideoAsset:
        video = await session.get(VideoAsset, video_id)
        if video is None or (video.metadata_json or {}).get(
            "source"
        ) != EXTERNAL_VIDEO_GENERATION_SOURCE:
            raise NotFoundError("video job not found")
        return video

    async def _mark_video_start_failed(
        self,
        session: AsyncSession,
        video_id: str,
        exc: Exception,
    ) -> None:
        await session.rollback()
        video = await session.get(VideoAsset, video_id)
        if video is None or (video.metadata_json or {}).get(
            "source"
        ) != EXTERNAL_VIDEO_GENERATION_SOURCE:
            return

        message = str(exc) or exc.__class__.__name__
        video.status = VideoStatus.FAILED.value
        video.error_message = message
        video.metadata_json = {
            **(video.metadata_json or {}),
            "implementation_status": "provider_start_failed",
            "provider_start_error": message,
        }
        await session.commit()
        await session.refresh(video)

    async def _requeue_failed_external_video_if_safe(
        self,
        session: AsyncSession,
        video: VideoAsset,
        *,
        external_request_id: str | None,
    ) -> GenerationTask | None:
        if not _should_requeue_failed_external_video(video):
            return None

        metadata = video.metadata_json or {}
        retry_count = _external_retry_count(metadata)
        if retry_count >= EXTERNAL_VIDEO_FAILED_REQUEUE_LIMIT:
            return None

        previous_error = _display_video_error(video.error_message)
        video.status = VideoStatus.REQUESTED.value
        video.error_message = None
        video.metadata_json = {
            **metadata,
            "implementation_status": "requeued_after_provider_start_failed",
            "previous_provider_start_error": previous_error,
            "external_retry_count": retry_count + 1,
        }
        return await self.task_service.create_task(
            session,
            queue_name=VIDEO_QUEUE_NAME,
            task_type=EXTERNAL_VIDEO_START_TASK_TYPE,
            business_type=EXTERNAL_VIDEO_GENERATION_SOURCE,
            business_id=video.id,
            campaign_id=video.campaign_id,
            payload={"video_id": video.id},
            max_attempts=1,
            metadata={
                "source": EXTERNAL_VIDEO_GENERATION_SOURCE,
                "external_request_id": external_request_id,
                "requeued_after_provider_start_failed": True,
                "external_retry_count": retry_count + 1,
            },
        )

    def _store_source_image(
        self,
        *,
        image: DecodedImage,
        campaign_id: str,
        asset_id: str,
    ) -> tuple[str, str | None]:
        if self.image_storage.settings.object_storage_provider != "local":
            encoded = base64.b64encode(image.data).decode("ascii")
            return f"data:{image.mime_type};base64,{encoded}", None

        extension = _extension_for_mime_type(image.mime_type)
        relative_path = Path("images") / EXTERNAL_VIDEO_GENERATION_SOURCE / campaign_id
        target_path = self.image_storage.storage_root / relative_path / f"{asset_id}{extension}"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(image.data)

        storage_key = f"local://{(relative_path / f'{asset_id}{extension}').as_posix()}"
        public_url = self.image_storage.public_url_for_storage_key(storage_key)
        if not public_url:
            raise AppError("failed to store source image")
        return public_url, storage_key

    def _job_read(self, video: VideoAsset) -> ExternalVideoGenerationJobRead:
        return ExternalVideoGenerationJobRead(
            job_id=video.id,
            status=_external_status(video),
            video_url=video.url
            if video.status in {VideoStatus.GENERATED.value, VideoStatus.APPROVED.value}
            else None,
            error_message=_display_video_error(video.error_message),
            duration_seconds=video.duration_seconds,
            aspect_ratio=video.aspect_ratio,
        )

    async def _start_video_generation_with_retries(
        self,
        session: AsyncSession,
        video: VideoAsset,
    ) -> VideoAsset:
        max_attempts = len(VIDEO_START_RETRY_DELAYS_SECONDS) + 1
        for attempt in range(1, max_attempts + 1):
            try:
                return await self.video_service.start_video_generation(session, video.id)
            except ProviderError as exc:
                if (
                    video.provider_job_id
                    or attempt >= max_attempts
                    or not _is_retryable_video_start_error(exc)
                ):
                    raise
                delay = VIDEO_START_RETRY_DELAYS_SECONDS[attempt - 1]
                if delay > 0:
                    await asyncio.sleep(delay)
        raise RuntimeError("video start retry loop exhausted unexpectedly")


def _decode_base64_image(value: str) -> DecodedImage:
    text = (value or "").strip()
    if not text:
        raise AppError("image base64 value is required")

    mime_type = ""
    encoded = text
    if text.startswith("data:"):
        header, separator, body = text.partition(",")
        if not separator or ";base64" not in header:
            raise AppError("image data URL must use base64 encoding")
        mime_type = header.removeprefix("data:").split(";", 1)[0].strip()
        encoded = body

    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise AppError("image must be valid base64") from exc
    if not data:
        raise AppError("image base64 value is required")

    sniffed_mime_type = _sniff_image_mime_type(data)
    mime_type = mime_type or sniffed_mime_type or "image/jpeg"
    if not mime_type.startswith("image/"):
        raise AppError("image data URL must use an image MIME type")
    return DecodedImage(data=data, mime_type=mime_type)


def _sniff_image_mime_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _extension_for_mime_type(mime_type: str) -> str:
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }.get(mime_type.lower(), ".jpg")


def _external_status(video: VideoAsset) -> str:
    if video.status in {VideoStatus.GENERATED.value, VideoStatus.APPROVED.value} and video.url:
        return "succeeded"
    if video.status == VideoStatus.FAILED.value:
        return "failed"
    return "processing"


def _is_retryable_video_start_error(exc: ProviderError) -> bool:
    return _is_retryable_video_start_message(str(exc))


def _is_retryable_video_start_message(message: str) -> bool:
    message = message.lower()
    retryable_markers = (
        "request failed",
        "timeout",
        "timed out",
        "connection",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        "internal server error",
        "returned 500",
        "returned 502",
        "returned 503",
        "returned 504",
    )
    return any(marker in message for marker in retryable_markers)


def _display_video_error(message: str | None) -> str | None:
    if message is None:
        return None
    normalized = message.strip()
    if not normalized:
        return None
    if normalized == LEGACY_EMPTY_VOLCENGINE_VIDEO_ERROR:
        return LEGACY_EMPTY_VOLCENGINE_VIDEO_ERROR_MESSAGE
    return normalized


def _should_requeue_failed_external_video(video: VideoAsset) -> bool:
    if video.status != VideoStatus.FAILED.value:
        return False
    if video.provider_job_id:
        return False
    message = _display_video_error(video.error_message)
    if not message:
        return False
    return _is_retryable_video_start_message(message)


def _external_retry_count(metadata: dict) -> int:
    try:
        return int(metadata.get("external_retry_count") or 0)
    except (TypeError, ValueError):
        return 0
