from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class VideoSourceImage:
    id: str
    url: str
    role: str = "reference_image"


@dataclass(frozen=True)
class VideoGenerationRequest:
    prompt: str
    source_images: list[VideoSourceImage]
    duration_seconds: int
    aspect_ratio: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class VideoGenerationStart:
    provider_job_id: str
    provider_status: str
    raw_response: dict
    request_payload: dict


@dataclass(frozen=True)
class VideoGenerationStatus:
    provider_job_id: str
    provider_status: str
    video_url: str | None = None
    last_frame_url: str | None = None
    error_message: str | None = None
    raw_response: dict = field(default_factory=dict)


class VideoProvider(Protocol):
    async def start_generation(self, request: VideoGenerationRequest) -> VideoGenerationStart:
        """Create an async video generation task with the upstream provider."""

    async def get_generation_status(self, provider_job_id: str) -> VideoGenerationStatus:
        """Fetch an async video generation task status from the upstream provider."""
