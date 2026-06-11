from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ProviderError
from backend.app.integrations.video.base import VideoProvider
from backend.app.integrations.video.placeholder_provider import PlaceholderVideoProvider
from backend.app.integrations.video.volcengine_provider import VolcengineVideoProvider


def get_video_provider(settings: Settings | None = None) -> VideoProvider:
    settings = settings or get_settings()
    if settings.video_provider == "placeholder":
        return PlaceholderVideoProvider()
    if settings.video_provider == "volcengine":
        api_key = (
            settings.volcengine_video_api_key
            or settings.volcengine_api_key
            or settings.ark_api_key
        )
        if not api_key:
            raise ProviderError(
                "VOLCENGINE_VIDEO_API_KEY, VOLCENGINE_API_KEY, or ARK_API_KEY is required "
                "when VIDEO_PROVIDER=volcengine."
            )
        return VolcengineVideoProvider(
            api_key=api_key,
            base_url=settings.volcengine_base_url,
            model=settings.volcengine_video_model,
            resolution=settings.volcengine_video_resolution,
            image_mode=settings.volcengine_video_image_mode,
            min_duration_seconds=settings.volcengine_video_min_duration_seconds,
            max_duration_seconds=settings.volcengine_video_max_duration_seconds,
            max_reference_images=settings.volcengine_video_max_reference_images,
            generate_audio=settings.volcengine_video_generate_audio,
            watermark=settings.volcengine_video_watermark,
            return_last_frame=settings.volcengine_video_return_last_frame,
            execution_expires_after=settings.volcengine_video_execution_expires_after,
            priority=settings.volcengine_video_priority,
            safety_identifier=settings.volcengine_video_safety_identifier,
        )
    raise ProviderError(f"Unsupported video provider: {settings.video_provider}")
