from uuid import uuid4

import httpx

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ProviderError
from backend.app.integrations.facebook.token_manager import FacebookTokenManager


class FacebookGraphClient:
    def __init__(
        self,
        settings: Settings | None = None,
        token_manager: FacebookTokenManager | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.token_manager = token_manager or FacebookTokenManager()

    def _url(self, path: str) -> str:
        base = self.settings.facebook_graph_api_base_url.rstrip("/")
        version = self.settings.facebook_graph_api_version.strip("/")
        path = path.lstrip("/")
        return f"{base}/{version}/{path}"

    async def publish_page_post(
        self,
        page_id: str,
        message: str,
        access_token_ref: str | None,
    ) -> dict:
        payload = {"message": message}
        if self.settings.facebook_dry_run:
            return {"id": f"dry_run_post_{uuid4()}", "dry_run": True, "payload": payload}

        access_token = await self.token_manager.resolve_token(access_token_ref)
        if not access_token:
            raise ProviderError("Facebook access token is required.")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self._url(f"{page_id}/feed"),
                data={**payload, "access_token": access_token},
            )
        if response.is_error:
            raise ProviderError(f"Facebook post failed: {response.text}")
        return response.json()

    async def publish_photo_post(
        self,
        page_id: str,
        image_url: str,
        caption: str,
        access_token_ref: str | None,
    ) -> dict:
        payload = {"url": image_url, "caption": caption}
        if self.settings.facebook_dry_run:
            return {"id": f"dry_run_photo_{uuid4()}", "dry_run": True, "payload": payload}

        access_token = await self.token_manager.resolve_token(access_token_ref)
        if not access_token:
            raise ProviderError("Facebook access token is required.")

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                self._url(f"{page_id}/photos"),
                data={**payload, "access_token": access_token},
            )
        if response.is_error:
            raise ProviderError(f"Facebook photo post failed: {response.text}")
        return response.json()
