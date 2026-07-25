import asyncio
import base64
import binascii
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from backend.app.core.errors import ProviderError
from backend.app.integrations.image.volcengine_provider import _prompt_from_brief
from backend.app.schemas.ai import GeneratedImage, ImageBrief

_SHARED_CPA_GEMINI_IMAGE_CLIENTS: dict[str, httpx.AsyncClient] = {}
_CPA_GEMINI_IMAGE_CLIENT_LIMITS = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=20,
)


def _normalize_cpa_gemini_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _shared_cpa_gemini_image_client(base_url: str) -> httpx.AsyncClient:
    normalized_base_url = _normalize_cpa_gemini_base_url(base_url)
    client = _SHARED_CPA_GEMINI_IMAGE_CLIENTS.get(normalized_base_url)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            limits=_CPA_GEMINI_IMAGE_CLIENT_LIMITS,
            timeout=None,
        )
        _SHARED_CPA_GEMINI_IMAGE_CLIENTS[normalized_base_url] = client
    return client


async def aclose_shared_cpa_gemini_image_clients() -> None:
    clients = list(_SHARED_CPA_GEMINI_IMAGE_CLIENTS.values())
    _SHARED_CPA_GEMINI_IMAGE_CLIENTS.clear()
    for client in clients:
        await client.aclose()


class CpaGeminiImageProvider:
    """Generate images through CLIProxyAPI's Gemini Chat Completions adapter."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        aspect_ratio: str = "9:16",
        storage_root: str = "storage",
        timeout_seconds: float = 300.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = _normalize_cpa_gemini_base_url(base_url)
        self.model = model
        self.aspect_ratio = aspect_ratio
        self.storage_root = Path(storage_root)
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client or _shared_cpa_gemini_image_client(self.base_url)

    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        return list(await asyncio.gather(*(self._generate_single(brief) for brief in briefs)))

    async def _generate_single(self, brief: ImageBrief) -> GeneratedImage:
        if brief.reference_image_data_url or brief.reference_image_url:
            raise ProviderError(
                "CPA Gemini image route currently supports text-to-image generation only."
            )

        prompt = brief.raw_prompt or _prompt_from_brief(brief)
        request_prompt = _with_aspect_ratio_instruction(prompt, self.aspect_ratio)
        response = await self._post_chat_completion(
            {
                "model": self.model,
                "stream": False,
                "modalities": ["image", "text"],
                "messages": [{"role": "user", "content": request_prompt}],
            }
        )
        storage_key = self._store_data_url(_image_data_url_from_response(response))
        return GeneratedImage(
            prompt=prompt,
            url=None,
            storage_key=storage_key,
            alt_text=brief.short_text,
            size=brief.size,
            metadata={
                "provider": "cpa_gemini",
                "model": self.model,
                "aspect_ratio": self.aspect_ratio,
                "image_index": brief.image_index,
                "brief_title": brief.title,
                "mode": "generate",
            },
        )

    async def _post_chat_completion(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http_client.post(
                f"{self.base_url}/chat/completions",
                json=request,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise ProviderError(
                f"CPA Gemini Chat API returned HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"CPA Gemini Chat API request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"CPA Gemini Chat API request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("CPA Gemini Chat API returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise ProviderError("CPA Gemini Chat API returned invalid image data.")
        return data

    def _store_data_url(self, data_url: str) -> str:
        header, separator, encoded = data_url.partition(",")
        if not header.startswith("data:image/") or not separator or ";base64" not in header:
            raise ProviderError("CPA Gemini Chat API returned an invalid image data URL.")
        mime_type = header.removeprefix("data:").split(";", 1)[0]
        try:
            image_bytes = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProviderError("CPA Gemini Chat API returned invalid image base64 data.") from exc
        if not image_bytes:
            raise ProviderError("CPA Gemini Chat API returned empty image data.")

        filename = f"{uuid4()}{_extension_from_mime_type(mime_type)}"
        relative_path = Path("images") / "cpa_gemini" / filename
        target_path = self.storage_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(image_bytes)
        return f"local://{relative_path.as_posix()}"


def _image_data_url_from_response(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list):
        raise ProviderError("CPA Gemini Chat API returned no image data URL.")
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        images = message.get("images")
        if not isinstance(images, list):
            continue
        for image in images:
            if not isinstance(image, dict):
                continue
            image_url = image.get("image_url")
            if not isinstance(image_url, dict):
                continue
            value = image_url.get("url")
            if isinstance(value, str) and value.startswith("data:"):
                return value
    raise ProviderError("CPA Gemini Chat API returned no image data URL.")


def _extension_from_mime_type(mime_type: str) -> str:
    if mime_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if mime_type == "image/webp":
        return ".webp"
    if mime_type == "image/gif":
        return ".gif"
    return ".png"

def _with_aspect_ratio_instruction(prompt: str, aspect_ratio: str) -> str:
    return (
        f"{prompt}\n\n"
        f"Output requirement: generate a vertical {aspect_ratio} portrait image. "
        "Keep all important subjects, logos, and text inside the safe area."
    )
