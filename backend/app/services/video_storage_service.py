from pathlib import Path, PurePosixPath
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
        public_url = self.public_url_for_storage_key(storage_key)
        if not public_url:
            raise ProviderError("Failed to build local video public URL.")
        return public_url, storage_key

    def public_url_for_storage_key(self, storage_key: str | None) -> str | None:
        if not storage_key or not storage_key.startswith("local://"):
            return None

        relative_path = _relative_path_from_storage_key(storage_key)
        return f"{self.settings.public_base_url.rstrip('/')}/storage/{relative_path}"

    def storage_key_for_public_url(self, url: str | None) -> str | None:
        if not url:
            return None

        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            return None
        if not parsed.path.startswith("/storage/"):
            return None

        storage_path = parsed.path.removeprefix("/storage/").lstrip("/")
        if not storage_path:
            return None

        try:
            relative_path = _relative_path_from_storage_key(f"local://{storage_path}")
        except ProviderError:
            return None
        return f"local://{relative_path}"

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


def _relative_path_from_storage_key(storage_key: str) -> str:
    raw_path = storage_key.removeprefix("local://").lstrip("/")
    relative_path = PurePosixPath(raw_path)
    if not raw_path or relative_path.is_absolute() or ".." in relative_path.parts:
        raise ProviderError("Invalid local video storage key.")
    return relative_path.as_posix()
