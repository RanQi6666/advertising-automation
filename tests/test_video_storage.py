from pathlib import Path

import pytest

from backend.app.core.config import Settings
from backend.app.db.models.video_asset import VideoAsset
from backend.app.services.video_service import VideoService
from backend.app.services.video_storage_service import VideoStorageService, _extension_from_url


def test_extension_from_url_ignores_query_string() -> None:
    assert _extension_from_url("https://example.com/generated/video.mp4?token=abc") == ".mp4"
    assert _extension_from_url("https://example.com/generated/video") == ".mp4"


@pytest.mark.asyncio
async def test_local_video_transfer_returns_public_url_and_storage_key(tmp_path: Path) -> None:
    settings = Settings(
        local_storage_root=str(tmp_path),
        public_base_url="http://api.test",
        object_storage_provider="local",
    )
    service = VideoStorageService(settings)

    async def fake_download(_: str, target_path: Path) -> None:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_bytes(b"video-bytes")

    service._download = fake_download  # type: ignore[method-assign]

    public_url, storage_key = await service.transfer_provider_video(
        source_url="https://provider.test/video.mp4?token=abc",
        video_id="video-1",
        provider_job_id="job-1",
    )

    assert public_url == "http://api.test/storage/videos/video-1/job-1.mp4"
    assert storage_key == "local://videos/video-1/job-1.mp4"
    assert (tmp_path / "videos" / "video-1" / "job-1.mp4").read_bytes() == b"video-bytes"


def test_public_url_for_local_video_storage_key_uses_current_base_url() -> None:
    service = VideoStorageService(
        Settings(
            public_base_url="http://127.0.0.1:8001",
            object_storage_provider="local",
        )
    )

    assert (
        service.public_url_for_storage_key("local://videos/video-1/job-1.mp4")
        == "http://127.0.0.1:8001/storage/videos/video-1/job-1.mp4"
    )
    assert service.public_url_for_storage_key("https://provider.test/video.mp4") is None


def test_video_service_normalizes_old_local_video_url() -> None:
    service = object.__new__(VideoService)
    service.video_storage = VideoStorageService(
        Settings(
            public_base_url="http://127.0.0.1:8001",
            object_storage_provider="local",
        )
    )
    video = VideoAsset(
        campaign_id="campaign-1",
        source_asset_ids=[],
        url="http://127.0.0.1:8000/storage/videos/video-1/job-1.mp4",
        storage_key="local://videos/video-1/job-1.mp4",
        aspect_ratio="9:16",
    )

    assert service._normalize_local_video_url(video) is True
    assert video.url == "http://127.0.0.1:8001/storage/videos/video-1/job-1.mp4"
    assert service._normalize_local_video_url(video) is False
