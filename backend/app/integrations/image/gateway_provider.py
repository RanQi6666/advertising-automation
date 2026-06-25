import base64
import binascii
from pathlib import Path
from typing import Any
from uuid import uuid4

from openai import AsyncOpenAI

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
    ) -> None:
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.provider_size = provider_size
        self.response_format = response_format
        self.extra_body = extra_body or {}
        self.storage_root = Path(storage_root)

    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        images: list[GeneratedImage] = []
        for brief in briefs:
            prompt = _prompt_from_brief(brief)
            request: dict[str, Any] = {
                "model": self.model,
                "prompt": prompt,
                "size": self.provider_size,
            }
            if self.response_format:
                request["response_format"] = self.response_format
            if self.extra_body:
                request["extra_body"] = self.extra_body

            response = await self.client.images.generate(**request)
            item = response.data[0] if response.data else None
            if item is None:
                raise ProviderError("Gateway image API returned no image data.")

            image_url = getattr(item, "url", None)
            b64_json = getattr(item, "b64_json", None)
            revised_prompt = getattr(item, "revised_prompt", None)
            if image_url:
                storage_key = f"gateway://{self.model}/{brief.image_index}"
            elif b64_json:
                storage_key = self._store_base64_image(b64_json)
            else:
                raise ProviderError("Gateway image API returned neither URL nor base64 data.")

            images.append(
                GeneratedImage(
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
            )
        return images

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
