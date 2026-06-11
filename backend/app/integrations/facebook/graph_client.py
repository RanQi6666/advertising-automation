import json
from pathlib import Path
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
        self.token_manager = token_manager or FacebookTokenManager(self.settings)

    def _url(self, path: str) -> str:
        base = self.settings.facebook_graph_api_base_url.rstrip("/")
        version = self.settings.facebook_graph_api_version.strip("/")
        path = path.lstrip("/")
        return f"{base}/{version}/{path}"

    def build_url(self, path: str) -> str:
        return self._url(path)

    @staticmethod
    def normalize_ad_account_id(ad_account_id: str) -> str:
        return ad_account_id if ad_account_id.startswith("act_") else f"act_{ad_account_id}"

    async def publish_page_post(
        self,
        page_id: str,
        message: str,
        access_token_ref: str | None,
    ) -> dict:
        payload = {"message": message}
        if self.settings.facebook_dry_run:
            return {
                "id": f"dry_run_post_{uuid4()}",
                "dry_run": True,
                "endpoint": self._url(f"{page_id}/feed"),
                "payload": payload,
            }

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
            return {
                "id": f"dry_run_photo_{uuid4()}",
                "dry_run": True,
                "endpoint": self._url(f"{page_id}/photos"),
                "payload": payload,
            }

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

    async def publish_video_post(
        self,
        page_id: str,
        video_url: str,
        description: str,
        access_token_ref: str | None,
        title: str | None = None,
        published: bool = True,
    ) -> dict:
        payload = {
            "file_url": video_url,
            "description": description,
            "published": published,
        }
        if title:
            payload["title"] = title
        if self.settings.facebook_dry_run:
            return {
                "id": f"dry_run_video_{uuid4()}",
                "dry_run": True,
                "endpoint": self._url(f"{page_id}/videos"),
                "payload": payload,
            }

        access_token = await self.token_manager.resolve_token(access_token_ref)
        if not access_token:
            raise ProviderError("Facebook access token is required.")

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self._url(f"{page_id}/videos"),
                data={**payload, "access_token": access_token},
            )
        if response.is_error:
            raise ProviderError(f"Facebook video post failed: {response.text}")
        return response.json()

    async def create_ad_video(
        self,
        ad_account_id: str,
        name: str,
        access_token_ref: str | None,
        video_url: str | None = None,
        video_file_path: Path | None = None,
    ) -> dict:
        if not video_url and not video_file_path:
            raise ProviderError("Facebook ad video upload requires a video URL or local file.")

        payload = {"name": name}
        if video_file_path:
            payload["source"] = video_file_path.name
        else:
            payload["file_url"] = video_url
        account_id = self.normalize_ad_account_id(ad_account_id)
        if self.settings.facebook_dry_run:
            return {
                "id": f"dry_run_advideo_{uuid4()}",
                "dry_run": True,
                "endpoint": self._url(f"{account_id}/advideos"),
                "payload": payload,
            }

        access_token = await self.token_manager.resolve_token(access_token_ref)
        if not access_token:
            raise ProviderError("Facebook access token is required.")

        if video_file_path:
            if not video_file_path.exists():
                raise ProviderError(f"Local video file does not exist: {video_file_path}")
            async with httpx.AsyncClient(timeout=120) as client:
                with video_file_path.open("rb") as video_file:
                    response = await client.post(
                        self._url(f"{account_id}/advideos"),
                        data={"name": name, "access_token": access_token},
                        files={"source": (video_file_path.name, video_file, "video/mp4")},
                    )
            if response.is_error:
                raise ProviderError(f"Facebook ad video upload failed: {response.text}")
            return response.json()

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self._url(f"{account_id}/advideos"),
                data={**payload, "access_token": access_token},
            )
        if response.is_error:
            raise ProviderError(f"Facebook ad video upload failed: {response.text}")
        return response.json()

    async def create_video_ad_creative(
        self,
        ad_account_id: str,
        page_id: str,
        video_id: str,
        name: str,
        message: str,
        title: str,
        access_token_ref: str | None,
        link_url: str | None = None,
        cta_type: str | None = None,
    ) -> dict:
        object_story_spec: dict = {
            "page_id": page_id,
            "video_data": {
                "video_id": video_id,
                "message": message,
                "title": title,
            },
        }
        if link_url and cta_type:
            object_story_spec["video_data"]["call_to_action"] = {
                "type": cta_type,
                "value": {"link": link_url},
            }

        payload = {
            "name": name,
            "object_story_spec": object_story_spec,
        }
        account_id = self.normalize_ad_account_id(ad_account_id)
        if self.settings.facebook_dry_run:
            return {
                "id": f"dry_run_adcreative_{uuid4()}",
                "dry_run": True,
                "endpoint": self._url(f"{account_id}/adcreatives"),
                "payload": payload,
            }

        access_token = await self.token_manager.resolve_token(access_token_ref)
        if not access_token:
            raise ProviderError("Facebook access token is required.")

        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                self._url(f"{account_id}/adcreatives"),
                data={
                    "name": name,
                    "object_story_spec": json.dumps(object_story_spec),
                    "access_token": access_token,
                },
            )
        if response.is_error:
            raise ProviderError(f"Facebook ad creative creation failed: {response.text}")
        return response.json()

    async def create_ad_campaign(
        self,
        ad_account_id: str,
        payload: dict,
        access_token_ref: str | None,
    ) -> dict:
        return await self._create_ad_object(
            ad_account_id=ad_account_id,
            edge="campaigns",
            payload=payload,
            access_token_ref=access_token_ref,
            dry_run_prefix="campaign",
        )

    async def create_adset(
        self,
        ad_account_id: str,
        payload: dict,
        access_token_ref: str | None,
    ) -> dict:
        return await self._create_ad_object(
            ad_account_id=ad_account_id,
            edge="adsets",
            payload=payload,
            access_token_ref=access_token_ref,
            dry_run_prefix="adset",
        )

    async def create_ad_creative(
        self,
        ad_account_id: str,
        payload: dict,
        access_token_ref: str | None,
    ) -> dict:
        return await self._create_ad_object(
            ad_account_id=ad_account_id,
            edge="adcreatives",
            payload=payload,
            access_token_ref=access_token_ref,
            dry_run_prefix="adcreative",
        )

    async def create_ad(
        self,
        ad_account_id: str,
        payload: dict,
        access_token_ref: str | None,
    ) -> dict:
        return await self._create_ad_object(
            ad_account_id=ad_account_id,
            edge="ads",
            payload=payload,
            access_token_ref=access_token_ref,
            dry_run_prefix="ad",
        )

    async def _create_ad_object(
        self,
        ad_account_id: str,
        edge: str,
        payload: dict,
        access_token_ref: str | None,
        dry_run_prefix: str,
    ) -> dict:
        account_id = self.normalize_ad_account_id(ad_account_id)
        endpoint = self._url(f"{account_id}/{edge}")
        if self.settings.facebook_dry_run:
            return {
                "id": f"dry_run_{dry_run_prefix}_{uuid4()}",
                "dry_run": True,
                "endpoint": endpoint,
                "payload": payload,
            }

        access_token = await self.token_manager.resolve_token(access_token_ref)
        if not access_token:
            raise ProviderError("Facebook access token is required.")

        data = {
            key: json.dumps(value) if isinstance(value, (dict, list)) else value
            for key, value in payload.items()
        }
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(endpoint, data={**data, "access_token": access_token})
        if response.is_error:
            raise ProviderError(f"Facebook {edge} creation failed: {response.text}")
        return response.json()
