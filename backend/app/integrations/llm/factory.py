from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ProviderError
from backend.app.integrations.llm.base import LLMProvider
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.integrations.llm.openai_provider import OpenAILLMProvider
from backend.app.integrations.llm.responses_provider import GatewayResponsesLLMProvider


def get_llm_provider(settings: Settings | None = None) -> LLMProvider:
    settings = settings or get_settings()
    if settings.llm_provider == "mock":
        return MockLLMProvider()
    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ProviderError("OPENAI_API_KEY is required when LLM_PROVIDER=openai.")
        return OpenAILLMProvider(
            api_key=settings.openai_api_key,
            model=settings.llm_model,
            base_url=settings.openai_base_url,
        )
    if settings.llm_provider == "gateway":
        api_key = settings.model_gateway_api_key or settings.openai_api_key
        base_url = settings.model_gateway_base_url or settings.openai_base_url
        model = settings.model_gateway_text_model or settings.llm_model
        if not api_key:
            raise ProviderError("MODEL_GATEWAY_API_KEY is required when LLM_PROVIDER=gateway.")
        if not base_url:
            raise ProviderError("MODEL_GATEWAY_BASE_URL is required when LLM_PROVIDER=gateway.")
        if not model:
            raise ProviderError("MODEL_GATEWAY_TEXT_MODEL is required when LLM_PROVIDER=gateway.")
        return GatewayResponsesLLMProvider(
            api_key=api_key,
            model=model,
            base_url=base_url,
        )
    if settings.llm_provider == "volcengine":
        if not settings.volcengine_api_key:
            raise ProviderError("VOLCENGINE_API_KEY is required when LLM_PROVIDER=volcengine.")
        return OpenAILLMProvider(
            api_key=settings.volcengine_api_key,
            model=settings.volcengine_model,
            base_url=settings.volcengine_base_url,
            supports_video_input=True,
            video_input_fps=settings.ad_performance_video_input_fps,
        )
    raise ProviderError(f"Unsupported LLM provider: {settings.llm_provider}")
