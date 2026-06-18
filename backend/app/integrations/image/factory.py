from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ProviderError
from backend.app.integrations.image.base import ImageProvider
from backend.app.integrations.image.placeholder_provider import PlaceholderImageProvider
from backend.app.integrations.image.volcengine_provider import VolcengineImageProvider


def get_image_provider(settings: Settings | None = None) -> ImageProvider:
    settings = settings or get_settings()
    if settings.image_provider == "placeholder":
        return PlaceholderImageProvider()
    if settings.image_provider == "volcengine":
        api_key = settings.volcengine_api_key or settings.ark_api_key
        if not api_key:
            raise ProviderError(
                "VOLCENGINE_API_KEY or ARK_API_KEY is required when IMAGE_PROVIDER=volcengine."
            )
        return VolcengineImageProvider(
            api_key=api_key,
            base_url=settings.volcengine_base_url,
            model=settings.volcengine_image_model,
            provider_size=settings.volcengine_image_size,
            watermark=settings.volcengine_image_watermark,
        )
    raise ProviderError(f"Unsupported image provider: {settings.image_provider}")
