import pytest

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
