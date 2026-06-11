from pathlib import Path
from urllib.parse import urlparse

import httpx

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ProviderError


class VideoStorageService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.storage_root = Path(self.settings.local_storage_root)

    async def transfer_provider_video(
        self,
        source_url: str,
        video_id: str,
        provider_job_id: str,
    ) -> tuple[str, str]:
        if self.settings.object_storage_provider != "local":
            raise ProviderError(
                f"Unsupported object storage provider: {self.settings.object_storage_provider}"
            )

        extension = _extension_from_url(source_url)
        relative_path = Path("videos") / video_id / f"{provider_job_id}{extension}"
        target_path = self.storage_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        await self._download(source_url, target_path)

        storage_key = f"local://{relative_path.as_posix()}"
        public_url = (
            f"{self.settings.public_base_url.rstrip('/')}/storage/{relative_path.as_posix()}"
        )
        return public_url, storage_key

    async def _download(self, source_url: str, target_path: Path) -> None:
        temp_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
        downloaded_bytes = 0
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.video_download_timeout_seconds,
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", source_url) as response:
                    response.raise_for_status()
                    with temp_path.open("wb") as file:
                        async for chunk in response.aiter_bytes():
                            downloaded_bytes += len(chunk)
                            if downloaded_bytes > self.settings.video_download_max_bytes:
                                raise ProviderError(
                                    "Downloaded video exceeds configured size limit."
                                )
                            file.write(chunk)
            temp_path.replace(target_path)
        except httpx.HTTPError as exc:
            if temp_path.exists():
                temp_path.unlink()
            raise ProviderError(f"Failed to download provider video: {exc}") from exc
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise


def _extension_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".mp4", ".mov", ".webm"} else ".mp4"
