import pytest

from backend.app.core.config import get_settings
from backend.app.services import external_image_route_service


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        value = self.values.get(key, 0) + 1
        self.values[key] = value
        return value

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_round_robin_routes_new_relay_nodes_with_their_pinned_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_redis = FakeRedis()
    monkeypatch.setenv("EXTERNAL_IMAGE_ROUTE_MODE", "round_robin")
    monkeypatch.setenv(
        "EXTERNAL_IMAGE_ROUTE_PROVIDERS",
        "dm_fox_gpt_image,newcli_gemini",
    )
    monkeypatch.setenv("DM_FOX_GPT_IMAGE_MODEL", "gpt-image-2")
    monkeypatch.setenv("NEWCLI_GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")
    get_settings.cache_clear()
    external_image_route_service.set_redis_client_factory_for_tests(lambda _url: fake_redis)
    try:
        routes = [
            await external_image_route_service.select_external_image_route()
            for _ in range(3)
        ]
    finally:
        external_image_route_service.set_redis_client_factory_for_tests(None)
        get_settings.cache_clear()

    assert [(route.sequence, route.provider, route.model) for route in routes] == [
        (1, "dm_fox_gpt_image", "gpt-image-2"),
        (2, "newcli_gemini", "gemini-3.1-flash-image"),
        (3, "dm_fox_gpt_image", "gpt-image-2"),
    ]


def test_newcli_gemini_route_restores_its_pinned_model() -> None:
    settings = get_settings().model_copy(update={"image_provider": "gateway"})
    route = external_image_route_service.ExternalImageRoute(
        sequence=2,
        provider="newcli_gemini",
        model="gemini-3.1-flash-image",
    )

    routed = external_image_route_service.settings_for_external_image_route(settings, route)

    assert routed.image_provider == "newcli_gemini"
    assert routed.newcli_gemini_image_model == "gemini-3.1-flash-image"
    assert external_image_route_service.route_from_metadata(
        {"image_route": route.as_metadata()}
    ) == route
