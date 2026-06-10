from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ProviderError
from backend.app.integrations.image.base import ImageProvider
from backend.app.integrations.image.placeholder_provider import PlaceholderImageProvider


def get_image_provider(settings: Settings | None = None) -> ImageProvider:
    settings = settings or get_settings()
    if settings.image_provider == "placeholder":
        return PlaceholderImageProvider()
    raise ProviderError(f"Unsupported image provider: {settings.image_provider}")
