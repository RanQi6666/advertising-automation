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


class GatewayImageProvider:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        provider_size: str,
        response_format: str | None = None,
        extra_body: dict[str, Any] | None = None,
        storage_root: str = "storage",
        timeout_seconds: float = 300.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self.model = model
        self.provider_size = provider_size
        self.response_format = response_format
        self.extra_body = extra_body or {}
        self.storage_root = Path(storage_root)

    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        results = await asyncio.gather(
            *(self._generate_single(brief) for brief in briefs)
        )
        return list(results)

    async def _generate_single(self, brief: ImageBrief) -> GeneratedImage:
        prompt = brief.raw_prompt or _prompt_from_brief(brief)
        request: dict[str, Any] = {
            "model": self.model,
            "prompt": prompt,
            "size": self.provider_size,
        }
        if self.response_format:
            request["response_format"] = self.response_format
        if self.extra_body:
            request.update(self.extra_body)

        response = await self._post_image_generation(request)
        data = response.get("data")
        item = data[0] if isinstance(data, list) and data else None
        if item is None:
            raise ProviderError("Gateway image API returned no image data.")

        image_url = item.get("url") if isinstance(item, dict) else None
        b64_json = item.get("b64_json") if isinstance(item, dict) else None
        revised_prompt = item.get("revised_prompt") if isinstance(item, dict) else None
        if image_url:
            storage_key = f"gateway://{self.model}/{brief.image_index}"
        elif b64_json:
            storage_key = self._store_base64_image(b64_json)
        else:
            raise ProviderError("Gateway image API returned neither URL nor base64 data.")

        return GeneratedImage(
            prompt=prompt,
            url=image_url,
            storage_key=storage_key,
            alt_text=brief.short_text,
            size=brief.size,
            metadata={
                "provider": "gateway",
                "model": self.model,
                "provider_size": self.provider_size,
                "response_format": self.response_format,
                "image_index": brief.image_index,
                "brief_title": brief.title,
                "revised_prompt": revised_prompt,
            },
        )

    async def _post_image_generation(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http_client.post(
                f"{self.base_url}/images/generations",
                json=request,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise ProviderError(
                f"Gateway image API returned HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Gateway image API request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Gateway image API request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Gateway image API returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise ProviderError("Gateway image API returned invalid image data.")
        return data

    def _store_base64_image(self, b64_json: str) -> str:
        raw_value = b64_json.strip()
        extension = ".png"
        if raw_value.startswith("data:"):
            header, _, encoded = raw_value.partition(",")
            raw_value = encoded
            if "image/jpeg" in header or "image/jpg" in header:
                extension = ".jpg"
            elif "image/webp" in header:
                extension = ".webp"

        try:
            image_bytes = base64.b64decode(raw_value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ProviderError("Gateway image API returned invalid base64 data.") from exc
        if not image_bytes:
            raise ProviderError("Gateway image API returned empty base64 data.")

        relative_path = Path("images") / "gateway" / f"{uuid4()}{extension}"
        target_path = self.storage_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(image_bytes)
        return f"local://{relative_path.as_posix()}"
