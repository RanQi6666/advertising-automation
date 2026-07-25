from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ProviderError
from backend.app.integrations.image.base import ImageProvider
from backend.app.integrations.image.cpa_gemini_provider import CpaGeminiImageProvider
from backend.app.integrations.image.gateway_provider import GatewayImageProvider
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
    if settings.image_provider == "cpa_gemini":
        api_key = settings.model_gateway_api_key or settings.openai_api_key
        base_url = settings.model_gateway_base_url or settings.openai_base_url
        if not api_key:
            raise ProviderError(
                "MODEL_GATEWAY_API_KEY is required when IMAGE_PROVIDER=cpa_gemini."
            )
        if not base_url:
            raise ProviderError(
                "MODEL_GATEWAY_BASE_URL is required when IMAGE_PROVIDER=cpa_gemini."
            )
        if not settings.model_gateway_gemini_image_model:
            raise ProviderError(
                "MODEL_GATEWAY_GEMINI_IMAGE_MODEL is required when IMAGE_PROVIDER=cpa_gemini."
            )
        return CpaGeminiImageProvider(
            api_key=api_key,
            base_url=base_url,
            model=settings.model_gateway_gemini_image_model,
            storage_root=settings.local_storage_root,
            timeout_seconds=settings.model_gateway_image_timeout_seconds,
        )
    if settings.image_provider == "gateway":
        api_key = settings.model_gateway_api_key or settings.openai_api_key
        base_url = settings.model_gateway_base_url or settings.openai_base_url
        if not api_key:
            raise ProviderError(
                "MODEL_GATEWAY_API_KEY is required when IMAGE_PROVIDER=gateway."
            )
        if not base_url:
            raise ProviderError(
                "MODEL_GATEWAY_BASE_URL is required when IMAGE_PROVIDER=gateway."
            )
        if not settings.model_gateway_image_model:
            raise ProviderError(
                "MODEL_GATEWAY_IMAGE_MODEL is required when IMAGE_PROVIDER=gateway."
            )
        return GatewayImageProvider(
            api_key=api_key,
            base_url=base_url,
            model=settings.model_gateway_image_model,
            provider_size=settings.model_gateway_image_size,
            response_format=settings.model_gateway_image_response_format,
            extra_body=settings.model_gateway_image_extra_body,
            storage_root=settings.local_storage_root,
            timeout_seconds=settings.model_gateway_image_timeout_seconds,
            edit_enabled=settings.model_gateway_image_edit_enabled,
            edit_path=settings.model_gateway_image_edit_path,
            edit_model=settings.model_gateway_image_edit_model,
        )
    raise ProviderError(f"Unsupported image provider: {settings.image_provider}")
