import asyncio
import base64
import importlib
import json

import httpx
import pytest

from backend.app.core.config import Settings
from backend.app.core.errors import ProviderError
from backend.app.integrations.image.factory import get_image_provider
from backend.app.integrations.image.gateway_provider import GatewayImageProvider
from backend.app.integrations.llm.factory import get_llm_provider
from backend.app.integrations.llm.responses_provider import GatewayResponsesLLMProvider
from backend.app.main import create_app
from backend.app.schemas.ai import ImageBrief
from backend.app.services.model_selection import (
    effective_image_model,
    effective_text_model,
    get_model_options,
    settings_for_image_model,
    settings_for_text_model,
)


def test_gateway_llm_provider_uses_model_gateway_settings() -> None:
    settings = Settings(
        llm_provider="gateway",
        model_gateway_api_key="gateway-key",
        model_gateway_base_url="http://127.0.0.1:3000/v1",
        model_gateway_text_model="gateway-text-model",
        model_gateway_text_timeout_seconds=240,
    )

    provider = get_llm_provider(settings)

    assert isinstance(provider, GatewayResponsesLLMProvider)
    assert provider.model == "gateway-text-model"
    assert provider.timeout_seconds == 240


@pytest.mark.asyncio
async def test_gateway_responses_provider_reuses_shared_client_per_base_url() -> None:
    first = GatewayResponsesLLMProvider(
        api_key="gateway-key",
        base_url="https://model.ggcss.xyz/v1",
        model="gpt-5.5",
    )
    second = GatewayResponsesLLMProvider(
        api_key="gateway-key",
        base_url="https://model.ggcss.xyz/v1/",
        model="gpt-5.5-mini",
    )
    third = GatewayResponsesLLMProvider(
        api_key="gateway-key",
        base_url="https://other-model.ggcss.xyz/v1",
        model="gpt-5.5",
    )

    try:
        assert first._http_client is second._http_client
        assert first._http_client is not third._http_client
        assert first._http_client.timeout.connect is None
        assert first._http_client.timeout.read is None
    finally:
        await _close_shared_gateway_clients_for_test()


@pytest.mark.asyncio
async def test_gateway_responses_provider_sends_fast_and_long_request_timeouts() -> None:
    captured_timeouts: list[dict[str, float | None]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json={"output_text": '{"summary":"OK"}'})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://model.ggcss.xyz/v1",
        timeout=None,
    ) as client:
        provider = GatewayResponsesLLMProvider(
            api_key="gateway-key",
            base_url="https://model.ggcss.xyz/v1",
            model="gpt-5.5",
            timeout_seconds=180,
            fast_timeout_seconds=45,
            http_client=client,
        )

        await provider._json_completion("Return JSON only.", '{"input":"Say OK"}')
        await provider.analyze_ad_performance({"metrics": {}, "creative": {}})

    assert captured_timeouts == [
        {"connect": 5.0, "read": 45.0, "write": 10.0, "pool": 5.0},
        {"connect": 5.0, "read": 180.0, "write": 10.0, "pool": 5.0},
    ]


@pytest.mark.asyncio
async def test_gateway_responses_llm_provider_posts_json_completion_to_responses() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": "gpt-5.5",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": '{"summary":"OK","root_causes":[]}',
                            }
                        ],
                    }
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://model.ggcss.xyz/v1",
    ) as client:
        provider = GatewayResponsesLLMProvider(
            api_key="gateway-key",
            base_url="https://model.ggcss.xyz/v1",
            model="gpt-5.5",
            http_client=client,
        )

        data = await provider._json_completion("Return JSON only.", '{"input":"Say OK"}')

    assert captured["path"] == "/v1/responses"
    assert captured["authorization"] == "Bearer gateway-key"
    assert captured["payload"]["model"] == "gpt-5.5"
    assert captured["payload"]["input"][0]["role"] == "system"
    assert captured["payload"]["input"][1]["role"] == "user"
    assert data == {"summary": "OK", "root_causes": []}


def test_gateway_image_factory_requires_image_model() -> None:
    settings = Settings(
        image_provider="gateway",
        model_gateway_api_key="gateway-key",
        model_gateway_base_url="http://127.0.0.1:3000/v1",
    )

    with pytest.raises(ProviderError, match="MODEL_GATEWAY_IMAGE_MODEL"):
        get_image_provider(settings)


@pytest.mark.asyncio
async def test_gateway_image_factory_uses_configured_image_timeout() -> None:
    settings = Settings(
        image_provider="gateway",
        model_gateway_api_key="gateway-key",
        model_gateway_base_url="http://127.0.0.1:3000/v1",
        model_gateway_image_model="gateway-image-model",
        model_gateway_image_timeout_seconds=345,
    )

    provider = get_image_provider(settings)

    assert isinstance(provider, GatewayImageProvider)
    try:
        assert provider.timeout_seconds == 345
        assert provider._http_client.timeout.read is None
    finally:
        await _close_shared_gateway_clients_for_test()


@pytest.mark.asyncio
async def test_gateway_image_provider_reuses_shared_client_per_base_url() -> None:
    first = GatewayImageProvider(
        api_key="gateway-key",
        base_url="http://127.0.0.1:3000/v1",
        model="gateway-image-model",
        provider_size="1024x1024",
    )
    second = GatewayImageProvider(
        api_key="gateway-key",
        base_url="http://127.0.0.1:3000/v1/",
        model="gateway-image-model-2",
        provider_size="1024x1024",
    )

    try:
        assert first._http_client is second._http_client
        assert first._http_client.timeout.read is None
    finally:
        await _close_shared_gateway_clients_for_test()


def test_model_options_expose_configured_gateway_models_without_keys() -> None:
    settings = Settings(
        llm_provider="gateway",
        image_provider="gateway",
        model_gateway_text_model="gpt-5.5",
        model_gateway_image_model="gpt-image-2",
        model_gateway_text_models=["gpt-5.5", "gpt-5.5-mini"],
        model_gateway_image_models=["gpt-image-2", "gpt-image-2-fast"],
        model_gateway_api_key="secret-key",
        model_gateway_base_url="http://127.0.0.1:8317/v1",
    )

    options = get_model_options(settings)

    assert [item.id for item in options.text] == [
        "gpt-5.5",
        "gpt-5.5-mini",
        "doubao-seed-2-0-pro",
    ]
    assert [item.id for item in options.image] == [
        "gpt-image-2",
        "gpt-image-2-fast",
        "doubao-seedream-4-5",
    ]
    assert options.defaults == {"text": "gpt-5.5", "image": "gpt-image-2"}
    assert options.text[0].is_default is True
    assert options.image[0].is_default is True
    assert "secret-key" not in options.model_dump_json()


def test_model_options_include_doubao_fallbacks_for_gateway_defaults() -> None:
    settings = Settings(
        llm_provider="gateway",
        image_provider="gateway",
        model_gateway_text_model="gpt-5.5",
        model_gateway_image_model="gpt-image-2",
        model_gateway_api_key="secret-key",
        model_gateway_base_url="http://127.0.0.1:8317/v1",
        volcengine_model="ep-configured-doubao-text",
        volcengine_image_model="doubao-seedream-4-5-251128",
    )

    options = get_model_options(settings)

    assert [(item.id, item.label, item.provider) for item in options.text] == [
        ("gpt-5.5", "gpt-5.5", "gateway"),
        ("doubao-seed-2-0-pro", "Doubao-Seed-2.0-pro", "volcengine"),
    ]
    assert [(item.id, item.label, item.provider) for item in options.image] == [
        ("gpt-image-2", "gpt-image-2", "gateway"),
        ("doubao-seedream-4-5", "Doubao-Seedream-4.5", "volcengine"),
    ]
    assert options.defaults == {"text": "gpt-5.5", "image": "gpt-image-2"}


def test_doubao_text_selector_routes_to_configured_volcengine_endpoint() -> None:
    settings = Settings(
        llm_provider="gateway",
        model_gateway_api_key="gateway-key",
        model_gateway_base_url="http://127.0.0.1:8317/v1",
        model_gateway_text_model="gpt-5.5",
        volcengine_api_key="volcengine-key",
        volcengine_model="ep-configured-doubao-text",
    )

    selected = settings_for_text_model(settings, "doubao-seed-2-0-pro")

    assert selected.llm_provider == "volcengine"
    assert selected.volcengine_model == "ep-configured-doubao-text"
    assert effective_text_model(selected) == "ep-configured-doubao-text"


def test_doubao_image_selector_routes_to_configured_volcengine_image_model() -> None:
    settings = Settings(
        image_provider="gateway",
        model_gateway_api_key="gateway-key",
        model_gateway_base_url="http://127.0.0.1:8317/v1",
        model_gateway_image_model="gpt-image-2",
        volcengine_api_key="volcengine-key",
        volcengine_image_model="doubao-seedream-4-5-251128",
    )

    selected = settings_for_image_model(settings, "doubao-seedream-4-5")

    assert selected.image_provider == "volcengine"
    assert selected.volcengine_image_model == "doubao-seedream-4-5-251128"
    assert effective_image_model(selected) == "doubao-seedream-4-5-251128"


@pytest.mark.asyncio
async def test_gateway_image_provider_supports_url_response() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["authorization"] = request.headers.get("authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "url": "https://images.example.test/generated.png",
                        "b64_json": None,
                        "revised_prompt": "revised",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:3000/v1",
    ) as client:
        provider = GatewayImageProvider(
            api_key="gateway-key",
            base_url="http://127.0.0.1:3000/v1",
            model="gateway-image-model",
            provider_size="1024x1024",
            response_format="url",
            http_client=client,
        )

        images = await provider.generate_images([_brief()])

    assert captured["path"] == "/v1/images/generations"
    assert captured["authorization"] == "Bearer gateway-key"
    assert captured["payload"]["model"] == "gateway-image-model"
    assert captured["payload"]["size"] == "1024x1024"
    assert captured["payload"]["response_format"] == "url"
    assert "Facebook" not in captured["payload"]["prompt"]
    assert images[0].url == "https://images.example.test/generated.png"
    assert images[0].storage_key == "gateway://gateway-image-model/1"
    assert images[0].metadata["provider"] == "gateway"


@pytest.mark.asyncio
async def test_gateway_image_provider_sends_configured_request_timeout() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json={"data": [{"url": "https://images.test/1.png"}]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:3000/v1",
        timeout=None,
    ) as client:
        provider = GatewayImageProvider(
            api_key="gateway-key",
            base_url="http://127.0.0.1:3000/v1",
            model="gateway-image-model",
            provider_size="1024x1024",
            timeout_seconds=345,
            http_client=client,
        )

        await provider.generate_images([_brief()])

    assert captured["timeout"] == {
        "connect": 345,
        "read": 345,
        "write": 345,
        "pool": 345,
    }


@pytest.mark.asyncio
async def test_gateway_image_provider_supports_base64_response(tmp_path) -> None:
    encoded = base64.b64encode(b"fake image bytes").decode("ascii")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"url": None, "b64_json": encoded}]})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:3000/v1",
    ) as client:
        provider = GatewayImageProvider(
            api_key="gateway-key",
            base_url="http://127.0.0.1:3000/v1",
            model="gateway-image-model",
            provider_size="1024x1024",
            storage_root=str(tmp_path),
            http_client=client,
        )

        images = await provider.generate_images([_brief()])

    assert images[0].url is None
    assert images[0].storage_key
    assert images[0].storage_key.startswith("local://images/gateway/")
    stored_path = tmp_path / images[0].storage_key.removeprefix("local://")
    assert stored_path.read_bytes() == b"fake image bytes"


@pytest.mark.asyncio
async def test_gateway_image_provider_generates_multiple_images_concurrently_in_order() -> None:
    slow_returned_after_fast_started = asyncio.Event()
    fast_started = asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        prompt = payload["prompt"]
        image_url = f"https://images.example.test/{prompt.replace(' ', '-')}.png"
        if prompt == "slow prompt":
            await asyncio.wait_for(fast_started.wait(), timeout=0.2)
            slow_returned_after_fast_started.set()
        elif prompt == "fast prompt":
            fast_started.set()
        return httpx.Response(
            200,
            json={
                "data": [
                    {"url": image_url}
                ]
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://127.0.0.1:3000/v1",
    ) as client:
        provider = GatewayImageProvider(
            api_key="gateway-key",
            base_url="http://127.0.0.1:3000/v1",
            model="gateway-image-model",
            provider_size="1024x1024",
            http_client=client,
        )

        images = await provider.generate_images(
            [
                _brief(image_index=1, raw_prompt="slow prompt"),
                _brief(image_index=2, raw_prompt="fast prompt"),
            ]
        )

    assert slow_returned_after_fast_started.is_set()
    assert [image.url for image in images] == [
        "https://images.example.test/slow-prompt.png",
        "https://images.example.test/fast-prompt.png",
    ]
    assert [image.metadata["image_index"] for image in images] == [1, 2]


@pytest.mark.asyncio
async def test_lifespan_closes_shared_gateway_clients(monkeypatch: pytest.MonkeyPatch) -> None:
    closed = False

    async def close_shared_clients() -> None:
        nonlocal closed
        closed = True

    monkeypatch.setattr(
        "backend.app.main.aclose_shared_gateway_clients",
        close_shared_clients,
        raising=False,
    )

    app = create_app()
    async with app.router.lifespan_context(app):
        pass

    assert closed is True


async def _close_shared_gateway_clients_for_test() -> None:
    try:
        module = importlib.import_module("backend.app.integrations.gateway_clients")
    except ModuleNotFoundError:
        return
    close_shared_clients = getattr(module, "aclose_shared_gateway_clients", None)
    if close_shared_clients is not None:
        await close_shared_clients()


def _brief(image_index: int = 1, raw_prompt: str | None = None) -> ImageBrief:
    return ImageBrief(
        image_index=image_index,
        title="Product scene",
        short_text="Try it today",
        visual_direction="Show a clean Facebook placement without Meta UI.",
        size="1:1",
        raw_prompt=raw_prompt,
    )
