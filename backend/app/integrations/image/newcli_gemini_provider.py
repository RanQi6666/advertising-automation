import asyncio
import base64
import binascii
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx

from backend.app.core.errors import ProviderError
from backend.app.integrations.image.volcengine_provider import _prompt_from_brief
from backend.app.schemas.ai import GeneratedImage, ImageBrief


class NewCliGeminiImageProvider:
    """Gemini native generateContent image adapter for the newcli relay."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        aspect_ratio: str,
        storage_root: str = "storage",
        timeout_seconds: float = 300.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.aspect_ratio = aspect_ratio
        self.storage_root = Path(storage_root)
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client or httpx.AsyncClient(timeout=None)

    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        return list(await asyncio.gather(*(self._generate_single(brief) for brief in briefs)))

    async def _generate_single(self, brief: ImageBrief) -> GeneratedImage:
        if brief.reference_image_data_url or brief.reference_image_url:
            raise ProviderError(
                "NewCLI Gemini image route currently supports text-to-image generation only."
            )

        prompt = brief.raw_prompt or _prompt_from_brief(brief)
        response = await self._post_generate_content(
            {
                "contents": [
                    {
                        "role": "user",
                        "parts": [{"text": prompt}],
                    }
                ],
                "generationConfig": {
                    "responseModalities": ["IMAGE"],
                    "imageConfig": {"aspectRatio": self.aspect_ratio},
                },
            }
        )
        mime_type, image_data = _image_data_from_response(response)
        storage_key = self._store_base64_image(mime_type, image_data)
        return GeneratedImage(
            prompt=prompt,
            url=None,
            storage_key=storage_key,
            alt_text=brief.short_text,
            size=brief.size,
            metadata={
                "provider": "newcli_gemini",
                "model": self.model,
                "aspect_ratio": self.aspect_ratio,
                "image_index": brief.image_index,
                "brief_title": brief.title,
                "mode": "generate",
            },
        )

    async def _post_generate_content(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http_client.post(
                self._generate_content_url(),
                json=request,
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise ProviderError(
                f"NewCLI Gemini API returned HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"NewCLI Gemini API request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"NewCLI Gemini API request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("NewCLI Gemini API returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise ProviderError("NewCLI Gemini API returned invalid image data.")
        return data

    def _generate_content_url(self) -> str:
        api_base = self.base_url if self.base_url.endswith("/v1beta") else f"{self.base_url}/v1beta"
        model = quote(self.model, safe="-._~")
        return f"{api_base}/models/{model}:generateContent"

    def _store_base64_image(self, mime_type: str, image_data: str) -> str:
        try:
            image_bytes = base64.b64decode(image_data, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProviderError("NewCLI Gemini API returned invalid image base64 data.") from exc
        if not image_bytes:
            raise ProviderError("NewCLI Gemini API returned empty image data.")

        filename = f"{uuid4()}{_extension_from_mime_type(mime_type)}"
        relative_path = Path("images") / "newcli_gemini" / filename
        target_path = self.storage_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(image_bytes)
        return f"local://{relative_path.as_posix()}"


def _image_data_from_response(response: dict[str, Any]) -> tuple[str, str]:
    candidates = response.get("candidates")
    if not isinstance(candidates, list):
        raise ProviderError("NewCLI Gemini API returned no image data.")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline_data = part.get("inlineData") or part.get("inline_data")
            if not isinstance(inline_data, dict):
                continue
            mime_type = inline_data.get("mimeType") or inline_data.get("mime_type")
            image_data = inline_data.get("data")
            if isinstance(mime_type, str) and isinstance(image_data, str):
                return mime_type, image_data
    raise ProviderError("NewCLI Gemini API returned no image data.")


def _extension_from_mime_type(mime_type: str) -> str:
    if mime_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if mime_type == "image/webp":
        return ".webp"
    if mime_type == "image/gif":
        return ".gif"
    return ".png"
