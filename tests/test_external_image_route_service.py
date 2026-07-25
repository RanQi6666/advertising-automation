import pytest
from pydantic import ValidationError

from backend.app.core.config import get_settings
from backend.app.services import external_image_route_service


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.closed = False

    async def incr(self, key: str) -> int:
        value = self.values.get(key, 0) + 1
        self.values[key] = value
        return value

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def round_robin_settings(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake_redis = FakeRedis()
    monkeypatch.setenv("EXTERNAL_IMAGE_ROUTE_MODE", "round_robin")
    monkeypatch.setenv("EXTERNAL_IMAGE_ROUTE_PROVIDERS", "gateway,volcengine")
    monkeypatch.setenv("MODEL_GATEWAY_IMAGE_MODEL", "gateway-image-model")
    monkeypatch.setenv("VOLCENGINE_IMAGE_MODEL", "volcengine-image-model")
    get_settings.cache_clear()
    external_image_route_service.set_redis_client_factory_for_tests(
        lambda _url: fake_redis
    )
    yield fake_redis
    external_image_route_service.set_redis_client_factory_for_tests(None)
    get_settings.cache_clear()


@pytest.fixture
def priority_fallback_settings(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    fake_redis = FakeRedis()
    monkeypatch.setenv("EXTERNAL_IMAGE_ROUTE_MODE", "priority_fallback")
    monkeypatch.setenv(
        "EXTERNAL_IMAGE_PRIORITY_PRIMARY_PROVIDERS",
        "jbb_gpt_image,dm_fox_gpt_image",
    )
    monkeypatch.setenv(
        "EXTERNAL_IMAGE_PRIORITY_FALLBACK_PROVIDERS",
        "cpa_gemini,volcengine",
    )
    monkeypatch.setenv("JBB_GPT_IMAGE_MODEL", "jbb-gpt-image-model")
    monkeypatch.setenv("DM_FOX_GPT_IMAGE_MODEL", "dm-fox-gpt-image-model")
    monkeypatch.setenv("MODEL_GATEWAY_GEMINI_IMAGE_MODEL", "cpa-gemini-image-model")
    monkeypatch.setenv("VOLCENGINE_IMAGE_MODEL", "volcengine-image-model")
    monkeypatch.setenv("EXTERNAL_IMAGE_GENERATION_MAX_ATTEMPTS", "3")
    get_settings.cache_clear()
    external_image_route_service.set_redis_client_factory_for_tests(
        lambda _url: fake_redis
    )
    yield fake_redis
    external_image_route_service.set_redis_client_factory_for_tests(None)
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_priority_fallback_initial_routes_strictly_alternate_primary_pair(
    priority_fallback_settings: FakeRedis,
) -> None:
    routes = [
        await external_image_route_service.select_external_image_route()
        for _ in range(4)
    ]

    assert [(route.strategy, route.sequence, route.provider, route.model) for route in routes] == [
        ("priority_fallback", 1, "jbb_gpt_image", "jbb-gpt-image-model"),
        ("priority_fallback", 2, "dm_fox_gpt_image", "dm-fox-gpt-image-model"),
        ("priority_fallback", 3, "jbb_gpt_image", "jbb-gpt-image-model"),
        ("priority_fallback", 4, "dm_fox_gpt_image", "dm-fox-gpt-image-model"),
    ]
    assert priority_fallback_settings.closed is True


@pytest.mark.parametrize(
    ("primary_providers", "fallback_providers", "max_attempts"),
    [
        ("dm_fox_gpt_image,jbb_gpt_image", "cpa_gemini,volcengine", "3"),
        ("jbb_gpt_image,volcengine", "cpa_gemini,dm_fox_gpt_image", "3"),
        ("jbb_gpt_image,dm_fox_gpt_image", "volcengine,cpa_gemini", "3"),
        ("jbb_gpt_image,dm_fox_gpt_image", "cpa_gemini,volcengine", "2"),
    ],
)
def test_priority_fallback_settings_reject_invalid_provider_order(
    monkeypatch: pytest.MonkeyPatch,
    primary_providers: str,
    fallback_providers: str,
    max_attempts: str,
) -> None:
    monkeypatch.setenv("EXTERNAL_IMAGE_ROUTE_MODE", "priority_fallback")
    monkeypatch.setenv("EXTERNAL_IMAGE_PRIORITY_PRIMARY_PROVIDERS", primary_providers)
    monkeypatch.setenv("EXTERNAL_IMAGE_PRIORITY_FALLBACK_PROVIDERS", fallback_providers)
    monkeypatch.setenv("EXTERNAL_IMAGE_GENERATION_MAX_ATTEMPTS", max_attempts)
    get_settings.cache_clear()

    with pytest.raises(ValidationError):
        get_settings()

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_round_robin_route_alternates_providers_and_uses_provider_models(
    round_robin_settings: FakeRedis,
) -> None:
    first = await external_image_route_service.select_external_image_route()
    second = await external_image_route_service.select_external_image_route()
    third = await external_image_route_service.select_external_image_route()

    assert (first.sequence, first.provider, first.model) == (
        1,
        "gateway",
        "gateway-image-model",
    )
    assert (second.sequence, second.provider, second.model) == (
        2,
        "volcengine",
        "volcengine-image-model",
    )
    assert (third.sequence, third.provider, third.model) == (
        3,
        "gateway",
        "gateway-image-model",
    )
    assert round_robin_settings.closed is True


def test_selected_route_overrides_global_image_provider(
    round_robin_settings: FakeRedis,
) -> None:
    settings = get_settings().model_copy(update={"image_provider": "gateway"})
    route = external_image_route_service.ExternalImageRoute(
        sequence=2,
        provider="volcengine",
        model="volcengine-image-model",
    )

    routed = external_image_route_service.settings_for_external_image_route(settings, route)

    assert routed.image_provider == "volcengine"
    assert routed.volcengine_image_model == "volcengine-image-model"

@pytest.mark.asyncio
async def test_round_robin_route_includes_cpa_gemini_with_its_own_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = FakeRedis()
    monkeypatch.setenv("EXTERNAL_IMAGE_ROUTE_MODE", "round_robin")
    monkeypatch.setenv("EXTERNAL_IMAGE_ROUTE_PROVIDERS", "gateway,volcengine,cpa_gemini")
    monkeypatch.setenv("MODEL_GATEWAY_IMAGE_MODEL", "gateway-image-model")
    monkeypatch.setenv("VOLCENGINE_IMAGE_MODEL", "volcengine-image-model")
    monkeypatch.setenv("MODEL_GATEWAY_GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
    get_settings.cache_clear()
    external_image_route_service.set_redis_client_factory_for_tests(
        lambda _url: fake_redis
    )
    try:
        routes = [
            await external_image_route_service.select_external_image_route()
            for _ in range(4)
        ]
    finally:
        external_image_route_service.set_redis_client_factory_for_tests(None)
        get_settings.cache_clear()

    assert [(route.sequence, route.provider, route.model) for route in routes] == [
        (1, "gateway", "gateway-image-model"),
        (2, "volcengine", "volcengine-image-model"),
        (3, "cpa_gemini", "gemini-3.1-flash-image"),
        (4, "gateway", "gateway-image-model"),
    ]


def test_cpa_gemini_route_restores_its_pinned_model() -> None:
    settings = get_settings().model_copy(update={"image_provider": "gateway"})
    route = external_image_route_service.ExternalImageRoute(
        sequence=3,
        provider="cpa_gemini",
        model="gemini-3.1-flash-image",
    )

    routed = external_image_route_service.settings_for_external_image_route(settings, route)

    assert routed.image_provider == "cpa_gemini"
    assert routed.model_gateway_gemini_image_model == "gemini-3.1-flash-image"
    assert external_image_route_service.route_from_metadata(
        {"image_route": route.as_metadata()}
    ) == route


@pytest.mark.asyncio
async def test_round_robin_route_includes_two_jbb_image_nodes_with_separate_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = FakeRedis()
    monkeypatch.setenv("EXTERNAL_IMAGE_ROUTE_MODE", "round_robin")
    monkeypatch.setenv(
        "EXTERNAL_IMAGE_ROUTE_PROVIDERS",
        "gateway,volcengine,cpa_gemini,jbb_grok,jbb_gpt_image",
    )
    monkeypatch.setenv("MODEL_GATEWAY_IMAGE_MODEL", "gateway-image-model")
    monkeypatch.setenv("VOLCENGINE_IMAGE_MODEL", "volcengine-image-model")
    monkeypatch.setenv("MODEL_GATEWAY_GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
    monkeypatch.setenv("JBB_GROK_IMAGE_MODEL", "grok-imagine-image-quality")
    monkeypatch.setenv("JBB_GPT_IMAGE_MODEL", "gpt-image-2")
    get_settings.cache_clear()
    external_image_route_service.set_redis_client_factory_for_tests(
        lambda _url: fake_redis
    )
    try:
        routes = [
            await external_image_route_service.select_external_image_route()
            for _ in range(5)
        ]
    finally:
        external_image_route_service.set_redis_client_factory_for_tests(None)
        get_settings.cache_clear()

    assert [(route.sequence, route.provider, route.model) for route in routes] == [
        (1, "gateway", "gateway-image-model"),
        (2, "volcengine", "volcengine-image-model"),
        (3, "cpa_gemini", "gemini-3.1-flash-image"),
        (4, "jbb_grok", "grok-imagine-image-quality"),
        (5, "jbb_gpt_image", "gpt-image-2"),
    ]


def test_jbb_route_restores_its_pinned_model() -> None:
    settings = get_settings().model_copy(update={"image_provider": "gateway"})
    route = external_image_route_service.ExternalImageRoute(
        sequence=4,
        provider="jbb_grok",
        model="grok-imagine-image-quality",
    )

    routed = external_image_route_service.settings_for_external_image_route(settings, route)

    assert routed.image_provider == "jbb_grok"
    assert routed.jbb_grok_image_model == "grok-imagine-image-quality"
    assert external_image_route_service.route_from_metadata(
        {"image_route": route.as_metadata()}
    ) == route


@pytest.mark.parametrize(
    ("provider", "error_code", "next_provider", "next_model"),
    [
        ("jbb_gpt_image", "provider_timeout", "cpa_gemini", "cpa-gemini-image-model"),
        ("dm_fox_gpt_image", "provider_429", "cpa_gemini", "cpa-gemini-image-model"),
        ("cpa_gemini", "unknown_provider_error", "volcengine", "volcengine-image-model"),
    ],
)
def test_priority_fallback_route_metadata_advances_technical_failures(
    priority_fallback_settings: FakeRedis,
    provider: str,
    error_code: str,
    next_provider: str,
    next_model: str,
) -> None:
    metadata = {
        "image_route": {
            "strategy": "priority_fallback",
            "sequence": 1,
            "provider": provider,
            "model": f"{provider}-model",
        }
    }

    updated = external_image_route_service.advance_priority_fallback_route_metadata(
        metadata,
        payload={"prompt": "test", "count": 1, "size": "9:16"},
        task_type="external_image_generate",
        attempt_count=1,
        error_code=error_code,
    )

    assert updated is not None
    assert updated["image_route"]["provider"] == next_provider
    assert updated["image_route"]["model"] == next_model
    assert updated["image_route_history"] == [
        {
            "attempt": 1,
            "provider": provider,
            "model": f"{provider}-model",
            "error_code": error_code,
            "next_provider": next_provider,
            "next_model": next_model,
        }
    ]
    assert metadata["image_route"]["provider"] == provider


@pytest.mark.parametrize(
    ("metadata", "payload", "task_type", "error_code"),
    [
        (
            {
                "image_route": {
                    "strategy": "priority_fallback",
                    "sequence": 1,
                    "provider": "volcengine",
                    "model": "volcengine-image-model",
                }
            },
            {"prompt": "test", "count": 1},
            "external_image_generate",
            "provider_timeout",
        ),
        (
            {
                "image_route": {
                    "strategy": "priority_fallback",
                    "sequence": 1,
                    "provider": "jbb_gpt_image",
                    "model": "jbb-gpt-image-model",
                }
            },
            {"prompt": "test", "count": 2},
            "external_image_generate",
            "provider_timeout",
        ),
        (
            {
                "image_route": {
                    "strategy": "priority_fallback",
                    "sequence": 1,
                    "provider": "jbb_gpt_image",
                    "model": "jbb-gpt-image-model",
                }
            },
            {"prompt": "test", "count": 1},
            "external_image_generate",
            "provider_400",
        ),
        (
            {
                "image_route": {
                    "strategy": "round_robin",
                    "sequence": 1,
                    "provider": "jbb_gpt_image",
                    "model": "jbb-gpt-image-model",
                }
            },
            {"prompt": "test", "count": 1},
            "external_image_generate",
            "provider_timeout",
        ),
    ],
)
def test_priority_fallback_route_metadata_does_not_advance_ineligible_tasks(
    priority_fallback_settings: FakeRedis,
    metadata: dict,
    payload: dict,
    task_type: str,
    error_code: str,
) -> None:
    assert (
        external_image_route_service.advance_priority_fallback_route_metadata(
            metadata,
            payload=payload,
            task_type=task_type,
            attempt_count=1,
            error_code=error_code,
        )
        is None
    )
