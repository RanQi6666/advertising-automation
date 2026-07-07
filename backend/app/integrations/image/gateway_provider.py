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

_SHARED_GATEWAY_IMAGE_CLIENTS: dict[str, httpx.AsyncClient] = {}
_GATEWAY_IMAGE_CLIENT_LIMITS = httpx.Limits(
    max_connections=100,
    max_keepalive_connections=20,
)


def _normalize_gateway_image_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _shared_gateway_image_client(base_url: str) -> httpx.AsyncClient:
    normalized_base_url = _normalize_gateway_image_base_url(base_url)
    client = _SHARED_GATEWAY_IMAGE_CLIENTS.get(normalized_base_url)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            limits=_GATEWAY_IMAGE_CLIENT_LIMITS,
            timeout=None,
        )
        _SHARED_GATEWAY_IMAGE_CLIENTS[normalized_base_url] = client
    return client


async def aclose_shared_gateway_image_clients() -> None:
    clients = list(_SHARED_GATEWAY_IMAGE_CLIENTS.values())
    _SHARED_GATEWAY_IMAGE_CLIENTS.clear()
    for client in clients:
        await client.aclose()


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
        edit_enabled: bool = False,
        edit_path: str = "/images/edits",
        edit_model: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = _normalize_gateway_image_base_url(base_url)
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client or _shared_gateway_image_client(self.base_url)
        self.model = model
        self.provider_size = provider_size
        self.response_format = response_format
        self.extra_body = extra_body or {}
        self.storage_root = Path(storage_root)
        self.edit_enabled = edit_enabled
        self.edit_path = _normalize_gateway_image_edit_path(edit_path)
        self.edit_model = edit_model or model

    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        results = await asyncio.gather(
            *(
                self._edit_single(brief)
                if self._should_edit(brief)
                else self._generate_single(brief)
                for brief in briefs
            )
        )
        return list(results)

    def _should_edit(self, brief: ImageBrief) -> bool:
        return bool(
            self.edit_enabled
            and (brief.reference_image_data_url or brief.reference_image_url)
        )

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
                "mode": "generate",
            },
        )

    async def _edit_single(self, brief: ImageBrief) -> GeneratedImage:
        prompt = brief.revision_instruction or brief.raw_prompt or _prompt_from_brief(brief)
        filename, image_bytes, mime_type = await self._reference_image_file(brief)
        request_data: dict[str, Any] = {
            "model": self.edit_model,
            "prompt": prompt,
            "size": self.provider_size,
        }
        if self.response_format:
            request_data["response_format"] = self.response_format
        if self.extra_body:
            request_data.update(self.extra_body)

        response = await self._post_image_edit(
            request_data,
            files={"image": (filename, image_bytes, mime_type)},
        )
        data = response.get("data")
        item = data[0] if isinstance(data, list) and data else None
        if item is None:
            raise ProviderError("Gateway image edit API returned no image data.")

        image_url = item.get("url") if isinstance(item, dict) else None
        b64_json = item.get("b64_json") if isinstance(item, dict) else None
        revised_prompt = item.get("revised_prompt") if isinstance(item, dict) else None
        if image_url:
            storage_key = f"gateway://{self.edit_model}/{brief.image_index}"
        elif b64_json:
            storage_key = self._store_base64_image(b64_json)
        else:
            raise ProviderError("Gateway image edit API returned neither URL nor base64 data.")

        return GeneratedImage(
            prompt=prompt,
            url=image_url,
            storage_key=storage_key,
            alt_text=brief.short_text,
            size=brief.size,
            metadata={
                "provider": "gateway",
                "model": self.edit_model,
                "base_model": self.model,
                "provider_size": self.provider_size,
                "response_format": self.response_format,
                "image_index": brief.image_index,
                "brief_title": brief.title,
                "revised_prompt": revised_prompt,
                "mode": "edit",
                "edit_path": self.edit_path,
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
                timeout=self.timeout_seconds,
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

    async def _post_image_edit(
        self,
        request_data: dict[str, Any],
        *,
        files: dict[str, tuple[str, bytes, str]],
    ) -> dict[str, Any]:
        try:
            response = await self._http_client.post(
                f"{self.base_url}{self.edit_path}",
                data=request_data,
                files=files,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise ProviderError(
                f"Gateway image edit API returned HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Gateway image edit API request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Gateway image edit API request failed: {exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderError("Gateway image edit API returned invalid JSON.") from exc
        if not isinstance(data, dict):
            raise ProviderError("Gateway image edit API returned invalid image data.")
        return data

    async def _reference_image_file(self, brief: ImageBrief) -> tuple[str, bytes, str]:
        if brief.reference_image_data_url:
            return _decode_reference_data_url(brief.reference_image_data_url)
        if brief.reference_image_url:
            return await self._download_reference_image(brief.reference_image_url)
        raise ProviderError("Gateway image edit requires a reference image.")

    async def _download_reference_image(self, image_url: str) -> tuple[str, bytes, str]:
        try:
            response = await self._http_client.get(image_url, timeout=self.timeout_seconds)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:1000]
            raise ProviderError(
                f"Reference image URL returned HTTP {exc.response.status_code}: {body}"
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(f"Reference image URL request timed out: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Reference image URL request failed: {exc}") from exc

        mime_type = response.headers.get("content-type", "image/png").split(";")[0]
        image_bytes = response.content
        if not image_bytes:
            raise ProviderError("Reference image URL returned empty image data.")
        return f"reference{_extension_from_mime_type(mime_type)}", image_bytes, mime_type

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


def _normalize_gateway_image_edit_path(edit_path: str) -> str:
    cleaned = (edit_path or "/images/edits").strip()
    if not cleaned.startswith("/"):
        cleaned = f"/{cleaned}"
    return cleaned


def _decode_reference_data_url(data_url: str) -> tuple[str, bytes, str]:
    header, separator, encoded = data_url.partition(",")
    if not data_url.startswith("data:") or not separator:
        raise ProviderError("Reference image data URL is invalid.")
    mime_type = header.removeprefix("data:").split(";")[0] or "image/png"
    try:
        image_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ProviderError("Reference image data URL contains invalid base64 data.") from exc
    if not image_bytes:
        raise ProviderError("Reference image data URL contains empty image data.")
    return f"reference{_extension_from_mime_type(mime_type)}", image_bytes, mime_type


def _extension_from_mime_type(mime_type: str) -> str:
    if mime_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if mime_type == "image/webp":
        return ".webp"
    if mime_type == "image/gif":
        return ".gif"
    return ".png"
