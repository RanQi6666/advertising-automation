import json

import httpx
import pytest

from backend.app.core.errors import ProviderError
from backend.app.schemas.ai import ImageBrief


@pytest.mark.asyncio
async def test_cpa_gemini_provider_posts_chat_request_and_stores_data_url(tmp_path) -> None:
    from backend.app.integrations.image.cpa_gemini_provider import CpaGeminiImageProvider

    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "images": [
                                {
                                    "image_url": {
                                        "url": "data:image/png;base64,aGVsbG8="
                                    }
                                }
                            ]
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CpaGeminiImageProvider(
            api_key="gateway-key",
            base_url="http://cpa.test/v1",
            model="gemini-3.1-flash-image",
            storage_root=str(tmp_path),
            http_client=client,
        )
        images = await provider.generate_images([_brief()])

    assert len(images) == 1
    assert images[0].url is None
    assert images[0].metadata["provider"] == "cpa_gemini"
    assert images[0].metadata["model"] == "gemini-3.1-flash-image"
    assert (tmp_path / images[0].storage_key.removeprefix("local://")).read_bytes() == b"hello"
    assert requests[0].url.path == "/v1/chat/completions"
    assert requests[0].headers["authorization"] == "Bearer gateway-key"
    assert json.loads(requests[0].content) == {
        "model": "gemini-3.1-flash-image",
        "stream": False,
        "modalities": ["image", "text"],
        "messages": [{"role": "user", "content": "Produce a product image."}],
    }


@pytest.mark.asyncio
async def test_cpa_gemini_provider_rejects_response_without_image_data_url(tmp_path) -> None:
    from backend.app.integrations.image.cpa_gemini_provider import CpaGeminiImageProvider

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "no image"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = CpaGeminiImageProvider(
            api_key="gateway-key",
            base_url="http://cpa.test/v1",
            model="gemini-3.1-flash-image",
            storage_root=str(tmp_path),
            http_client=client,
        )
        with pytest.raises(ProviderError, match="data URL"):
            await provider.generate_images([_brief()])


def _brief() -> ImageBrief:
    return ImageBrief(
        image_index=1,
        title="Product scene",
        short_text="Try it today",
        visual_direction="Show the product in use.",
        raw_prompt="Produce a product image.",
    )
