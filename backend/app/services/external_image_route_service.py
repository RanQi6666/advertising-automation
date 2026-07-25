from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError, ProviderError
from backend.app.services.model_selection import effective_image_model

EXTERNAL_IMAGE_ROUTE_REDIS_KEY = "external_image_generation:round_robin"
ImageRouteProvider = Literal[
    "gateway",
    "volcengine",
    "cpa_gemini",
    "jbb_grok",
    "jbb_gpt_image",
    "dm_fox_gpt_image",
    "newcli_gemini",
]

_redis_client_factory_for_tests: Callable[[str], object] | None = None


@dataclass(frozen=True, slots=True)
class ExternalImageRoute:
    sequence: int
    provider: ImageRouteProvider
    model: str

    def as_metadata(self) -> dict[str, str | int]:
        return {
            "strategy": "round_robin",
            "sequence": self.sequence,
            "provider": self.provider,
            "model": self.model,
        }


def set_redis_client_factory_for_tests(factory: Callable[[str], object] | None) -> None:
    global _redis_client_factory_for_tests
    _redis_client_factory_for_tests = factory


async def select_external_image_route(
    settings: Settings | None = None,
) -> ExternalImageRoute:
    settings = settings or get_settings()
    if settings.external_image_route_mode != "round_robin":
        raise AppError("External image round-robin routing is not enabled.")

    providers = settings.external_image_route_providers
    if not providers:
        raise AppError("EXTERNAL_IMAGE_ROUTE_PROVIDERS must contain at least one provider.")

    client = _make_redis_client(settings)
    try:
        sequence = int(await client.incr(EXTERNAL_IMAGE_ROUTE_REDIS_KEY))
    except Exception as exc:
        raise ProviderError("External image route counter is unavailable.") from exc
    finally:
        await _close_redis_client(client)

    provider = providers[(sequence - 1) % len(providers)]
    routed_settings = settings_for_external_image_route(
        settings,
        ExternalImageRoute(sequence=sequence, provider=provider, model=""),
    )
    model = effective_image_model(routed_settings)
    if not model:
        raise AppError(f"No image model configured for route provider: {provider}.")
    return ExternalImageRoute(sequence=sequence, provider=provider, model=model)


def settings_for_external_image_route(
    settings: Settings,
    route: ExternalImageRoute,
) -> Settings:
    updates: dict[str, str] = {"image_provider": route.provider}
    if route.provider == "gateway" and route.model:
        updates["model_gateway_image_model"] = route.model
    if route.provider == "volcengine" and route.model:
        updates["volcengine_image_model"] = route.model
    if route.provider == "cpa_gemini" and route.model:
        updates["model_gateway_gemini_image_model"] = route.model
    if route.provider == "jbb_grok" and route.model:
        updates["jbb_grok_image_model"] = route.model
    if route.provider == "jbb_gpt_image" and route.model:
        updates["jbb_gpt_image_model"] = route.model
    if route.provider == "dm_fox_gpt_image" and route.model:
        updates["dm_fox_gpt_image_model"] = route.model
    if route.provider == "newcli_gemini" and route.model:
        updates["newcli_gemini_image_model"] = route.model
    return settings.model_copy(update=updates)


def route_from_metadata(metadata: dict | None) -> ExternalImageRoute | None:
    route_data = (metadata or {}).get("image_route")
    if not isinstance(route_data, dict):
        return None
    provider = route_data.get("provider")
    model = route_data.get("model")
    sequence = route_data.get("sequence")
    supported_providers = {
        "gateway",
        "volcengine",
        "cpa_gemini",
        "jbb_grok",
        "jbb_gpt_image",
        "dm_fox_gpt_image",
        "newcli_gemini",
    }
    if provider not in supported_providers or not isinstance(model, str) or not model:
        return None
    try:
        parsed_sequence = int(sequence)
    except (TypeError, ValueError):
        return None
    if parsed_sequence < 1:
        return None
    return ExternalImageRoute(
        sequence=parsed_sequence,
        provider=provider,
        model=model,
    )


def _make_redis_client(settings: Settings) -> object:
    if _redis_client_factory_for_tests is not None:
        return _redis_client_factory_for_tests(settings.redis_url)

    import redis.asyncio as redis

    return redis.Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=5,
    )


async def _close_redis_client(client: object) -> None:
    aclose = getattr(client, "aclose", None)
    if callable(aclose):
        await aclose()
        return
    close = getattr(client, "close", None)
    if callable(close):
        result = close()
        if hasattr(result, "__await__"):
            await result
