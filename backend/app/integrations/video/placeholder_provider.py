from pathlib import Path
from uuid import uuid4

from backend.app.core.config import get_settings
from backend.app.integrations.video.base import (
    VideoGenerationRequest,
    VideoGenerationStart,
    VideoGenerationStatus,
)


class PlaceholderVideoProvider:
    async def start_generation(self, request: VideoGenerationRequest) -> VideoGenerationStart:
        provider_job_id = f"placeholder-video-{uuid4()}"
        payload = {
            "prompt": request.prompt,
            "duration_seconds": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            "source_images": [
                {"id": image.id, "url": image.url, "role": image.role}
                for image in request.source_images
            ],
        }
        return VideoGenerationStart(
            provider_job_id=provider_job_id,
            provider_status="queued",
            raw_response={"id": provider_job_id, "status": "queued", "provider": "placeholder"},
            request_payload=payload,
        )

    async def get_generation_status(self, provider_job_id: str) -> VideoGenerationStatus:
        settings = get_settings()
        relative_path = f"videos/placeholder/{provider_job_id}.mp4"
        target_path = Path(settings.local_storage_root) / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"placeholder video")
        video_url = f"{settings.public_base_url.rstrip('/')}/storage/{relative_path}"
        return VideoGenerationStatus(
            provider_job_id=provider_job_id,
            provider_status="succeeded",
            video_url=video_url,
            raw_response={
                "id": provider_job_id,
                "status": "succeeded",
                "provider": "placeholder",
                "content": {"video_url": video_url},
            },
        )
