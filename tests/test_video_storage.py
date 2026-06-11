from pathlib import Path

import pytest

from backend.app.core.config import Settings
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
