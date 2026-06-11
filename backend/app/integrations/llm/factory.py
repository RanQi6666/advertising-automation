from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ProviderError
from backend.app.integrations.llm.base import LLMProvider
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.integrations.llm.openai_provider import OpenAILLMProvider


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
    if settings.llm_provider == "volcengine":
        if not settings.volcengine_api_key:
            raise ProviderError("VOLCENGINE_API_KEY is required when LLM_PROVIDER=volcengine.")
        return OpenAILLMProvider(
            api_key=settings.volcengine_api_key,
            model=settings.volcengine_model,
            base_url=settings.volcengine_base_url,
        )
    raise ProviderError(f"Unsupported LLM provider: {settings.llm_provider}")
