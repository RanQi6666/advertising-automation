import json

import httpx
import pytest

from backend.app.core.config import get_settings
from backend.app.integrations.image.factory import get_image_provider
from backend.app.integrations.image.gateway_provider import GatewayImageProvider
from backend.app.schemas.ai import ImageBrief


def test_dm_fox_gpt_image_uses_its_own_openai_compatible_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER", "dm_fox_gpt_image")
    monkeypatch.setenv("DM_FOX_GPT_IMAGE_API_KEY", "test-dm-fox-key")
    monkeypatch.setenv("DM_FOX_GPT_IMAGE_BASE_URL", "https://dm-fox.example.test/codex/v1")
    monkeypatch.setenv("DM_FOX_GPT_IMAGE_MODEL", "gpt-image-2")
    monkeypatch.setenv("DM_FOX_GPT_IMAGE_QUALITY", "high")
    get_settings.cache_clear()

    try:
        provider = get_image_provider()
    finally:
        get_settings.cache_clear()

    assert isinstance(provider, GatewayImageProvider)
    assert provider.api_key == "test-dm-fox-key"
    assert provider.base_url == "https://dm-fox.example.test/codex/v1"
    assert provider.model == "gpt-image-2"
    assert provider.provider_size == "1024x1024"
    assert provider.extra_body == {"quality": "high"}
    assert provider.provider_name == "dm_fox_gpt_image"
    assert provider.edit_enabled is False


@pytest.mark.asyncio
async def test_newcli_gemini_provider_posts_native_generate_content_request_and_stores_image(
    tmp_path,
) -> None:
    from backend.app.integrations.image.newcli_gemini_provider import (
        NewCliGeminiImageProvider,
    )

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Generated image."},
                                {
                                    "inlineData": {
                                        "mimeType": "image/png",
                                        "data": "aGVsbG8=",
                                    }
                                },
                            ]
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = NewCliGeminiImageProvider(
            api_key="test-newcli-key",
            base_url="https://code.newcli.example/gemini",
            model="gemini-3.1-flash-image",
            aspect_ratio="3:2",
            storage_root=str(tmp_path),
            http_client=client,
        )
        images = await provider.generate_images([_brief()])

    assert len(images) == 1
    assert images[0].url is None
    assert images[0].metadata["provider"] == "newcli_gemini"
    assert images[0].metadata["model"] == "gemini-3.1-flash-image"
    assert images[0].metadata["aspect_ratio"] == "3:2"
    assert (tmp_path / images[0].storage_key.removeprefix("local://")).read_bytes() == b"hello"
    assert requests[0].url.path == "/gemini/v1beta/models/gemini-3.1-flash-image:generateContent"
    assert requests[0].headers["x-goog-api-key"] == "test-newcli-key"
    assert json.loads(requests[0].content) == {
        "contents": [{"role": "user", "parts": [{"text": "Produce a product image."}]}],
        "generationConfig": {
            "responseModalities": ["IMAGE"],
            "imageConfig": {"aspectRatio": "3:2"},
        },
    }


def _brief() -> ImageBrief:
    return ImageBrief(
        image_index=1,
        title="Product scene",
        short_text="Try it today",
        visual_direction="Show the product in use.",
        raw_prompt="Produce a product image.",
    )

@pytest.mark.asyncio
async def test_dm_fox_gpt_image_request_includes_high_quality() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"url": "https://cdn.example.test/image.png"}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = GatewayImageProvider(
            api_key="test-dm-fox-key",
            base_url="https://dm-fox.example.test/codex/v1",
            model="gpt-image-2",
            provider_size="1024x1024",
            extra_body={"quality": "high"},
            provider_name="dm_fox_gpt_image",
            http_client=client,
        )
        images = await provider.generate_images([_brief()])

    assert images[0].metadata["provider"] == "dm_fox_gpt_image"
    assert requests[0].url.path == "/codex/v1/images/generations"
    assert json.loads(requests[0].content) == {
        "model": "gpt-image-2",
        "prompt": "Produce a product image.",
        "size": "1024x1024",
        "quality": "high",
    }
