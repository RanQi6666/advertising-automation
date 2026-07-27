from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError, ProviderError
from backend.app.services.model_selection import effective_image_model

EXTERNAL_IMAGE_ROUTE_REDIS_KEY = "external_image_generation:round_robin"
EXTERNAL_IMAGE_PRIORITY_REDIS_KEY = "external_image_generation:priority_primary"
ImageRouteProvider = Literal[
    "gateway",
    "volcengine",
    "cpa_gemini",
    "jbb_grok",
    "jbb_gpt_image",
    "dm_fox_gpt_image",
    "alita_gpt_image",
    "newcli_gemini",
]
ImageRouteStrategy = Literal["round_robin", "priority_fallback"]
PRIORITY_FALLBACK_ERROR_CODES = {
    "provider_timeout",
    "provider_429",
    "unknown_provider_error",
}
PRIORITY_FALLBACK_NEXT_PROVIDER: dict[ImageRouteProvider, ImageRouteProvider] = {
    "jbb_gpt_image": "cpa_gemini",
    "dm_fox_gpt_image": "cpa_gemini",
    "alita_gpt_image": "cpa_gemini",
    "cpa_gemini": "volcengine",
}

_redis_client_factory_for_tests: Callable[[str], object] | None = None


@dataclass(frozen=True, slots=True)
class ExternalImageRoute:
    sequence: int
    provider: ImageRouteProvider
    model: str
    strategy: ImageRouteStrategy = "round_robin"

    def as_metadata(self) -> dict[str, str | int]:
        return {
            "strategy": self.strategy,
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
    mode = settings.external_image_route_mode
    if mode == "round_robin":
        providers = settings.external_image_route_providers
        redis_key = EXTERNAL_IMAGE_ROUTE_REDIS_KEY
        strategy: ImageRouteStrategy = "round_robin"
    elif mode == "priority_fallback":
        providers = settings.external_image_priority_primary_providers
        redis_key = EXTERNAL_IMAGE_PRIORITY_REDIS_KEY
        strategy = "priority_fallback"
    else:
        raise AppError("External image routing is not enabled.")
    if not providers:
        raise AppError("EXTERNAL_IMAGE_ROUTE_PROVIDERS must contain at least one provider.")

    client = _make_redis_client(settings)
    try:
        sequence = int(await client.incr(redis_key))
    except Exception as exc:
        raise ProviderError("External image route counter is unavailable.") from exc
    finally:
        await _close_redis_client(client)

    provider = providers[(sequence - 1) % len(providers)]
    routed_settings = settings_for_external_image_route(
        settings,
        ExternalImageRoute(
            sequence=sequence,
            provider=provider,
            model="",
            strategy=strategy,
        ),
    )
    model = effective_image_model(routed_settings)
    if not model:
        raise AppError(f"No image model configured for route provider: {provider}.")
    return ExternalImageRoute(
        sequence=sequence,
        provider=provider,
        model=model,
        strategy=strategy,
    )


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
    if route.provider == "alita_gpt_image" and route.model:
        updates["alita_gpt_image_model"] = route.model
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
    strategy = route_data.get("strategy", "round_robin")
    supported_providers = {
        "gateway",
        "volcengine",
        "cpa_gemini",
        "jbb_grok",
        "jbb_gpt_image",
        "dm_fox_gpt_image",
        "alita_gpt_image",
        "newcli_gemini",
    }
    if (
        provider not in supported_providers
        or not isinstance(model, str)
        or not model
        or strategy not in {"round_robin", "priority_fallback"}
    ):
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
        strategy=strategy,
    )


def advance_priority_fallback_route_metadata(
    metadata: dict | None,
    *,
    payload: dict | None,
    task_type: str,
    attempt_count: int,
    error_code: str,
    settings: Settings | None = None,
) -> dict | None:
    if task_type != "external_image_generate":
        return None
    if error_code not in PRIORITY_FALLBACK_ERROR_CODES:
        return None

    route = route_from_metadata(metadata)
    if route is None or route.strategy != "priority_fallback":
        return None

    next_provider = PRIORITY_FALLBACK_NEXT_PROVIDER.get(route.provider)
    if next_provider is None:
        return None

    settings = settings or get_settings()
    next_route = ExternalImageRoute(
        sequence=route.sequence,
        provider=next_provider,
        model="",
        strategy="priority_fallback",
    )
    next_model = effective_image_model(
        settings_for_external_image_route(settings, next_route)
    )
    if not next_model:
        raise AppError(f"No image model configured for route provider: {next_provider}.")

    updated_metadata = deepcopy(metadata or {})
    updated_metadata["image_route"] = ExternalImageRoute(
        sequence=route.sequence,
        provider=next_provider,
        model=next_model,
        strategy="priority_fallback",
    ).as_metadata()
    history = updated_metadata.setdefault("image_route_history", [])
    if not isinstance(history, list):
        history = []
        updated_metadata["image_route_history"] = history
    history.append(
        {
            "attempt": attempt_count,
            "provider": route.provider,
            "model": route.model,
            "error_code": error_code,
            "next_provider": next_provider,
            "next_model": next_model,
        }
    )
    return updated_metadata


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
