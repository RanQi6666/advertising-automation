from pathlib import Path

import pytest

from backend.app.core.config import Settings
from backend.app.services.image_storage_service import ImageStorageService, _extension_from_url


def test_image_extension_from_url_ignores_query_string() -> None:
    assert _extension_from_url("https://example.com/generated/image.jpeg?token=abc") == ".jpeg"
    assert _extension_from_url("https://example.com/generated/image.webp") == ".webp"
    assert _extension_from_url("https://example.com/generated/image") == ".jpg"


def test_public_url_helpers_round_trip_local_image_storage_key() -> None:
    service = ImageStorageService(
        Settings(
            public_base_url="http://api.test",
            object_storage_provider="local",
        )
    )

    storage_key = "local://images/campaign-1/image-1.png"
    assert service.public_url_for_storage_key(storage_key) == "http://api.test/storage/images/campaign-1/image-1.png"
    assert (
        service.storage_key_for_public_url(
            "http://old-host/storage/images/campaign-1/image-1.png?token=abc"
        )
        == storage_key
    )


def test_data_url_for_storage_key_reads_local_image(tmp_path: Path) -> None:
    settings = Settings(
        local_storage_root=str(tmp_path),
        public_base_url="http://api.test",
        object_storage_provider="local",
    )
    service = ImageStorageService(settings)
    image_path = tmp_path / "images" / "campaign-1" / "image-1.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image-bytes")

    data_url = service.data_url_for_storage_key("local://images/campaign-1/image-1.png")

    assert data_url == "data:image/png;base64,aW1hZ2UtYnl0ZXM="


def test_data_url_for_storage_key_returns_none_for_missing_image(tmp_path: Path) -> None:
    service = ImageStorageService(
        Settings(
            local_storage_root=str(tmp_path),
            public_base_url="http://api.test",
            object_storage_provider="local",
        )
    )

    assert service.data_url_for_storage_key("local://images/missing.jpeg") is None


@pytest.mark.asyncio
async def test_local_image_transfer_returns_public_url_and_storage_key(tmp_path: Path) -> None:
    settings = Settings(
        local_storage_root=str(tmp_path),
        public_base_url="http://api.test",
        object_storage_provider="local",
    )
    service = ImageStorageService(settings)

    async def fake_download(_: str, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"image-bytes")

    service._download = fake_download  # type: ignore[method-assign]

    public_url, storage_key = await service.transfer_provider_image(
        source_url="https://provider.test/image.png?token=abc",
        campaign_id="campaign-1",
        image_id="image-1",
    )

    assert public_url == "http://api.test/storage/images/campaign-1/image-1.png"
    assert storage_key == "local://images/campaign-1/image-1.png"
    assert (tmp_path / "images" / "campaign-1" / "image-1.png").read_bytes() == b"image-bytes"
