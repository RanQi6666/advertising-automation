import re
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from uuid import uuid4

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

    def store_uploaded_reference_video(
        self,
        data: bytes,
        content_type: str | None,
        filename: str | None = None,
    ) -> str:
        if self.settings.object_storage_provider != "local":
            raise ProviderError(
                f"Unsupported object storage provider: {self.settings.object_storage_provider}"
            )
        if not data:
            raise ProviderError("Uploaded reference video is empty.")

        extension = _reference_video_extension(content_type, filename)
        upload_asset_id = str(uuid4())
        relative_path = (
            Path("videos")
            / "storyboard_reference_uploads"
            / f"{upload_asset_id}{extension}"
        )
        target_path = self.storage_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)
        return upload_asset_id

    def path_for_uploaded_reference_video(self, upload_asset_id: str) -> Path:
        if not _OPAQUE_UPLOAD_ID.fullmatch(upload_asset_id):
            raise ProviderError("Invalid reference video upload asset id.")
        upload_root = self.storage_root / "videos" / "storyboard_reference_uploads"
        matches = [
            path
            for path in upload_root.glob(f"{upload_asset_id}.*")
            if path.is_file() and path.suffix.lower() in _VIDEO_EXTENSIONS
        ]
        if len(matches) != 1:
            raise ProviderError("Uploaded reference video was not found.")
        return matches[0]

    def path_for_storage_key(self, storage_key: str | None) -> Path | None:
        if not storage_key or not storage_key.startswith("local://"):
            return None
        relative_path = _relative_path_from_storage_key(storage_key)
        path = self.storage_root / Path(relative_path)
        return path if path.is_file() else None

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
    return suffix if suffix in _VIDEO_EXTENSIONS else ".mp4"


def _reference_video_extension(content_type: str | None, filename: str | None) -> str:
    normalized = (content_type or "").split(";", 1)[0].strip().lower()
    by_content_type = {
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/webm": ".webm",
    }
    if normalized in by_content_type:
        return by_content_type[normalized]
    suffix = Path(filename or "").suffix.lower()
    if suffix in _VIDEO_EXTENSIONS:
        return suffix
    raise ProviderError("Reference video must be MP4, MOV, or WebM.")


def _relative_path_from_storage_key(storage_key: str) -> str:
    raw_path = storage_key.removeprefix("local://").lstrip("/")
    relative_path = PurePosixPath(raw_path)
    if not raw_path or relative_path.is_absolute() or ".." in relative_path.parts:
        raise ProviderError("Invalid local video storage key.")
    return relative_path.as_posix()


_VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm"}
_OPAQUE_UPLOAD_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
