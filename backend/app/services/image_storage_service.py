import base64
import mimetypes
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import ProviderError


class ImageStorageService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.storage_root = Path(self.settings.local_storage_root)

    async def transfer_provider_image(
        self,
        source_url: str,
        campaign_id: str,
        image_id: str,
    ) -> tuple[str, str]:
        if self.settings.object_storage_provider != "local":
            raise ProviderError(
                f"Unsupported object storage provider: {self.settings.object_storage_provider}"
            )

        extension = _extension_from_url(source_url)
        relative_path = Path("images") / campaign_id / f"{image_id}{extension}"
        target_path = self.storage_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        await self._download(source_url, target_path)

        storage_key = f"local://{relative_path.as_posix()}"
        public_url = self.public_url_for_storage_key(storage_key)
        if not public_url:
            raise ProviderError("Failed to build local image public URL.")
        return public_url, storage_key

    async def transfer_external_image(
        self,
        source_url: str | None,
        job_id: str,
        image_index: int,
        *,
        source_storage_key: str | None = None,
    ) -> tuple[str, str]:
        if self.settings.object_storage_provider != "local":
            raise ProviderError(
                f"Unsupported object storage provider: {self.settings.object_storage_provider}"
            )

        extension = _external_image_extension(source_url, source_storage_key)
        relative_path = (
            Path("images")
            / "external_image_generation"
            / job_id
            / f"{max(int(image_index), 1)}{extension}"
        )
        target_path = self.storage_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        if source_storage_key and source_storage_key.startswith("local://"):
            source_relative_path = _relative_path_from_storage_key(source_storage_key)
            source_path = self.storage_root / Path(source_relative_path)
            if not source_path.exists():
                raise ProviderError("Provider image storage key does not exist locally.")
            if source_path.resolve() != target_path.resolve():
                shutil.copyfile(source_path, target_path)
        elif source_url:
            await self._download(source_url, target_path)
        else:
            raise ProviderError("Image provider returned no image URL or storage key.")

        storage_key = f"local://{relative_path.as_posix()}"
        public_url = self.public_url_for_storage_key(storage_key)
        if not public_url:
            raise ProviderError("Failed to build external image public URL.")
        return public_url, storage_key

    def store_uploaded_source_image(
        self,
        data: bytes,
        content_type: str | None,
    ) -> tuple[str, str]:
        if self.settings.object_storage_provider != "local":
            raise ProviderError(
                f"Unsupported object storage provider: {self.settings.object_storage_provider}"
            )
        if not data:
            raise ProviderError("Uploaded source image is empty.")

        extension = _extension_from_content_type(content_type)
        relative_path = (
            Path("images") / "external_image_source" / f"{uuid4()}{extension}"
        )
        target_path = self.storage_root / relative_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(data)

        storage_key = f"local://{relative_path.as_posix()}"
        public_url = self.public_url_for_storage_key(storage_key)
        if not public_url:
            raise ProviderError("Failed to build uploaded source image public URL.")
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

    def data_url_for_storage_key(self, storage_key: str | None) -> str | None:
        if not storage_key or not storage_key.startswith("local://"):
            return None

        try:
            relative_path = _relative_path_from_storage_key(storage_key)
        except ProviderError:
            return None

        source_path = self.storage_root / Path(relative_path)
        try:
            image_bytes = source_path.read_bytes()
        except OSError:
            return None

        mime_type = _mime_type_from_path(source_path)
        encoded = base64.b64encode(image_bytes).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    async def _download(self, source_url: str, target_path: Path) -> None:
        temp_path = target_path.with_suffix(f"{target_path.suffix}.tmp")
        downloaded_bytes = 0
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.image_download_timeout_seconds,
                follow_redirects=True,
            ) as client:
                async with client.stream("GET", source_url) as response:
                    response.raise_for_status()
                    with temp_path.open("wb") as file:
                        async for chunk in response.aiter_bytes():
                            downloaded_bytes += len(chunk)
                            if downloaded_bytes > self.settings.image_download_max_bytes:
                                raise ProviderError(
                                    "Downloaded image exceeds configured size limit."
                                )
                            file.write(chunk)
            temp_path.replace(target_path)
        except httpx.HTTPError as exc:
            if temp_path.exists():
                temp_path.unlink()
            raise ProviderError(f"Failed to download provider image: {exc}") from exc
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise


def _extension_from_url(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in _IMAGE_EXTENSIONS else ".jpg"


def _extension_from_content_type(content_type: str | None) -> str:
    normalized = (content_type or "").split(";")[0].strip().lower()
    if normalized in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    if normalized == "image/png":
        return ".png"
    if normalized == "image/webp":
        return ".webp"
    if normalized == "image/gif":
        return ".gif"
    if normalized == "image/svg+xml":
        return ".svg"
    return ".jpg"


def _mime_type_from_path(path: Path) -> str:
    guessed_type, _ = mimetypes.guess_type(path.name)
    if guessed_type and guessed_type.startswith("image/"):
        return guessed_type
    return "image/jpeg"


def _relative_path_from_storage_key(storage_key: str) -> str:
    raw_path = storage_key.removeprefix("local://").lstrip("/\\")
    relative_path = PurePosixPath(raw_path)
    if not raw_path or relative_path.is_absolute() or ".." in relative_path.parts:
        raise ProviderError("Invalid local image storage key.")
    return relative_path.as_posix()


def _external_image_extension(source_url: str | None, source_storage_key: str | None) -> str:
    if source_url:
        return _extension_from_url(source_url)
    if source_storage_key:
        suffix = Path(_relative_path_from_storage_key(source_storage_key)).suffix.lower()
        if suffix in _IMAGE_EXTENSIONS:
            return suffix
    return ".jpg"


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg"}
