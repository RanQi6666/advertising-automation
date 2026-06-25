import base64
from types import SimpleNamespace

import pytest

from backend.app.core.config import Settings
from backend.app.core.errors import ProviderError
from backend.app.integrations.image.factory import get_image_provider
from backend.app.integrations.image.gateway_provider import GatewayImageProvider
from backend.app.integrations.llm.factory import get_llm_provider
from backend.app.integrations.llm.openai_provider import OpenAILLMProvider
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
    )

    provider = get_llm_provider(settings)

    assert isinstance(provider, OpenAILLMProvider)
    assert provider.model == "gateway-text-model"


def test_gateway_image_factory_requires_image_model() -> None:
    settings = Settings(
        image_provider="gateway",
        model_gateway_api_key="gateway-key",
        model_gateway_base_url="http://127.0.0.1:3000/v1",
    )

    with pytest.raises(ProviderError, match="MODEL_GATEWAY_IMAGE_MODEL"):
        get_image_provider(settings)


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
    provider = GatewayImageProvider(
        api_key="gateway-key",
        base_url="http://127.0.0.1:3000/v1",
        model="gateway-image-model",
        provider_size="1024x1024",
        response_format="url",
    )
    captured: dict = {}

    async def fake_generate(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            data=[
                SimpleNamespace(
                    url="https://images.example.test/generated.png",
                    b64_json=None,
                    revised_prompt="revised",
                )
            ]
        )

    provider.client = SimpleNamespace(images=SimpleNamespace(generate=fake_generate))

    images = await provider.generate_images([_brief()])

    assert captured["model"] == "gateway-image-model"
    assert captured["size"] == "1024x1024"
    assert captured["response_format"] == "url"
    assert "Facebook" not in captured["prompt"]
    assert images[0].url == "https://images.example.test/generated.png"
    assert images[0].storage_key == "gateway://gateway-image-model/1"
    assert images[0].metadata["provider"] == "gateway"


@pytest.mark.asyncio
async def test_gateway_image_provider_supports_base64_response(tmp_path) -> None:
    provider = GatewayImageProvider(
        api_key="gateway-key",
        base_url="http://127.0.0.1:3000/v1",
        model="gateway-image-model",
        provider_size="1024x1024",
        storage_root=str(tmp_path),
    )
    encoded = base64.b64encode(b"fake image bytes").decode("ascii")

    async def fake_generate(**kwargs):
        return SimpleNamespace(data=[SimpleNamespace(url=None, b64_json=encoded)])

    provider.client = SimpleNamespace(images=SimpleNamespace(generate=fake_generate))

    images = await provider.generate_images([_brief()])

    assert images[0].url is None
    assert images[0].storage_key
    assert images[0].storage_key.startswith("local://images/gateway/")
    stored_path = tmp_path / images[0].storage_key.removeprefix("local://")
    assert stored_path.read_bytes() == b"fake image bytes"


def _brief() -> ImageBrief:
    return ImageBrief(
        image_index=1,
        title="Product scene",
        short_text="Try it today",
        visual_direction="Show a clean Facebook placement without Meta UI.",
        size="1:1",
    )
