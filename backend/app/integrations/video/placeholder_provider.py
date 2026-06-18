from uuid import uuid4

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
        return VideoGenerationStatus(
            provider_job_id=provider_job_id,
            provider_status="succeeded",
            video_url=f"https://example.com/videos/{provider_job_id}.mp4",
            raw_response={
                "id": provider_job_id,
                "status": "succeeded",
                "provider": "placeholder",
                "content": {"video_url": f"https://example.com/videos/{provider_job_id}.mp4"},
            },
        )
