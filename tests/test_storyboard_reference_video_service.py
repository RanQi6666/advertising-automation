import asyncio
from pathlib import Path

import pytest

from backend.app.core.config import Settings
from backend.app.core.errors import AppError, ProviderError
from backend.app.db.models.video_asset import VideoAsset
from backend.app.schemas.external_ai_generation import (
    ExternalAIReferenceVideoAsset,
    ExternalAIReferenceVideoUpload,
    ExternalAIReferenceVideoURL,
)
from backend.app.services.safe_public_http import DownloadedFile
from backend.app.services.storyboard_reference_video_service import (
    StoryboardReferenceVideoService,
    _reference_frame_timestamps,
)
from backend.app.services.video_storage_service import VideoStorageService


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "local_storage_root": str(tmp_path / "storage"),
        "public_base_url": "http://127.0.0.1:8001",
        "storyboard_reference_video_max_duration_seconds": 30,
        "storyboard_reference_video_sample_interval_seconds": 2,
        "storyboard_reference_video_allow_private_hosts": False,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("duration", "expected"),
    [
        (5.8, [0.0, 2.0, 4.0, 5.8]),
        (6.0, [0.0, 2.0, 4.0, 6.0]),
        (1.25, [0.0, 1.25]),
    ],
)
def test_reference_frame_timestamps_include_exact_final_without_duplicates(
    duration: float,
    expected: list[float],
) -> None:
    assert _reference_frame_timestamps(duration, 2.0) == expected


def test_reference_video_url_is_downloaded_and_frames_are_extracted_sequentially(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    download_options: dict[str, object] = {}

    async def fake_download(url: str, destination: Path, **kwargs: object) -> DownloadedFile:
        events.append(f"download:{url}")
        download_options.update(kwargs)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"video")
        return DownloadedFile(url, url, destination, "video/mp4", 5)

    async def fake_probe(_source: Path, *, timeout_seconds: float) -> dict[str, object]:
        events.append(f"probe:{timeout_seconds:g}")
        return {
            "duration_seconds": 5.8,
            "format": "mov,mp4,m4a,3gp,3g2,mj2",
            "codec": "h264",
        }

    active_extractions = 0

    async def fake_extract(
        _source: Path,
        destination: Path,
        timestamp_seconds: float,
        *,
        is_final: bool,
        width: int,
        jpeg_quality: int,
        timeout_seconds: float,
    ) -> Path:
        nonlocal active_extractions
        assert active_extractions == 0
        active_extractions += 1
        events.append(f"extract:{timestamp_seconds:g}:{is_final}")
        assert width == 768
        assert jpeg_quality == 4
        assert timeout_seconds == 15
        await asyncio.sleep(0)
        destination.write_bytes(f"jpeg-{timestamp_seconds:g}".encode())
        active_extractions -= 1
        return destination

    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service.download_public_http_file",
        fake_download,
    )
    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service._probe_reference_video",
        fake_probe,
    )
    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service._extract_reference_video_frame",
        fake_extract,
    )

    service = StoryboardReferenceVideoService(settings=_settings(tmp_path))
    prepared = asyncio.run(
        service.prepare(
            None,
            ExternalAIReferenceVideoURL(
                source_type="url",
                video_url="https://cdn.example.test/reference.mp4",
            ),
            task_id="task-reference-url",
        )
    )

    assert [frame.timestamp_seconds for frame in prepared.frames] == [0.0, 2.0, 4.0, 5.8]
    assert all(frame.image_url.startswith("data:image/jpeg;base64,") for frame in prepared.frames)
    assert events == [
        "download:https://cdn.example.test/reference.mp4",
        "probe:10",
        "extract:0:False",
        "extract:2:False",
        "extract:4:False",
        "extract:5.8:True",
    ]
    assert download_options["allow_private_networks"] is False
    assert download_options["max_bytes"] == service.settings.video_download_max_bytes


def test_reference_video_over_30_seconds_stops_before_frame_extraction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_download(url: str, destination: Path, **_kwargs: object) -> DownloadedFile:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"video")
        return DownloadedFile(url, url, destination, "video/mp4", 5)

    async def fake_probe(_source: Path, *, timeout_seconds: float) -> dict[str, object]:
        del timeout_seconds
        return {"duration_seconds": 30.01, "format": "mp4", "codec": "h264"}

    extracted = False

    async def should_not_extract(*_args: object, **_kwargs: object) -> Path:
        nonlocal extracted
        extracted = True
        raise AssertionError("frame extraction must not run")

    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service.download_public_http_file",
        fake_download,
    )
    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service._probe_reference_video",
        fake_probe,
    )
    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service._extract_reference_video_frame",
        should_not_extract,
    )

    with pytest.raises(AppError, match="exceeds the 30 second limit"):
        asyncio.run(
            StoryboardReferenceVideoService(settings=_settings(tmp_path)).prepare(
                None,
                ExternalAIReferenceVideoURL(
                    source_type="url",
                    video_url="https://cdn.example.test/too-long.mp4",
                ),
                task_id="task-too-long",
            )
        )

    assert extracted is False


def test_reference_video_rejects_unsupported_probed_format(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def fake_download(url: str, destination: Path, **_kwargs: object) -> DownloadedFile:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"video")
        return DownloadedFile(url, url, destination, "application/octet-stream", 5)

    async def fake_probe(_source: Path, *, timeout_seconds: float) -> dict[str, object]:
        del timeout_seconds
        return {"duration_seconds": 6.0, "format": "avi", "codec": "mpeg4"}

    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service.download_public_http_file",
        fake_download,
    )
    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service._probe_reference_video",
        fake_probe,
    )

    with pytest.raises(AppError, match="MP4, MOV, or WebM"):
        asyncio.run(
            StoryboardReferenceVideoService(settings=_settings(tmp_path)).prepare(
                None,
                ExternalAIReferenceVideoURL(
                    source_type="url",
                    video_url="https://cdn.example.test/disguised.mp4",
                ),
                task_id="task-bad-format",
            )
        )


def test_reference_video_missing_extracted_frame_fails_the_whole_analysis(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "storage" / "videos" / "storyboard_reference_uploads" / "upload-1.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"video")

    async def fake_probe(_source: Path, *, timeout_seconds: float) -> dict[str, object]:
        del timeout_seconds
        return {"duration_seconds": 4.0, "format": "mp4", "codec": "h264"}

    async def fake_extract(
        _source: Path,
        destination: Path,
        timestamp_seconds: float,
        **_kwargs: object,
    ) -> Path:
        if timestamp_seconds == 2.0:
            raise ProviderError("ffmpeg could not extract reference frame at 2.00s")
        destination.write_bytes(b"jpeg")
        return destination

    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service._probe_reference_video",
        fake_probe,
    )
    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service._extract_reference_video_frame",
        fake_extract,
    )

    with pytest.raises(ProviderError, match="2.00s"):
        asyncio.run(
            StoryboardReferenceVideoService(settings=_settings(tmp_path)).prepare(
                None,
                ExternalAIReferenceVideoUpload(
                    source_type="uploaded_asset",
                    upload_asset_id="upload-1",
                ),
                task_id="task-missing-frame",
            )
        )


def test_video_storage_resolves_uploaded_reference_and_rejects_opaque_id_traversal(
    tmp_path: Path,
) -> None:
    storage = VideoStorageService(settings=_settings(tmp_path))
    upload_id = storage.store_uploaded_reference_video(b"video", "video/mp4", "sample.mp4")

    resolved = storage.path_for_uploaded_reference_video(upload_id)

    assert resolved.exists()
    assert resolved.read_bytes() == b"video"
    with pytest.raises(ProviderError, match="Invalid reference video upload asset id"):
        storage.path_for_uploaded_reference_video("../outside")


def test_existing_video_asset_prefers_local_storage_key_over_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    local_path = tmp_path / "storage" / "videos" / "campaign" / "asset.mp4"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    local_path.write_bytes(b"local-video")
    asset = VideoAsset(
        id="video-asset-1",
        campaign_id="campaign-1",
        storage_key="local://videos/campaign/asset.mp4",
        url="https://cdn.example.test/fallback.mp4",
    )

    class FakeSession:
        async def get(self, model: type[VideoAsset], asset_id: str) -> VideoAsset | None:
            assert model is VideoAsset
            assert asset_id == "video-asset-1"
            return asset

    async def fake_probe(source: Path, *, timeout_seconds: float) -> dict[str, object]:
        assert source == local_path
        del timeout_seconds
        return {"duration_seconds": 2.0, "format": "mp4", "codec": "h264"}

    async def fake_extract(
        _source: Path,
        destination: Path,
        _timestamp_seconds: float,
        **_kwargs: object,
    ) -> Path:
        destination.write_bytes(b"jpeg")
        return destination

    async def should_not_download(*_args: object, **_kwargs: object) -> DownloadedFile:
        raise AssertionError("asset URL must not be downloaded when storage_key is available")

    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service._probe_reference_video",
        fake_probe,
    )
    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service._extract_reference_video_frame",
        fake_extract,
    )
    monkeypatch.setattr(
        "backend.app.services.storyboard_reference_video_service.download_public_http_file",
        should_not_download,
    )

    prepared = asyncio.run(
        StoryboardReferenceVideoService(settings=settings).prepare(
            FakeSession(),
            ExternalAIReferenceVideoAsset(
                source_type="video_asset",
                video_asset_id="video-asset-1",
            ),
            task_id="task-existing-asset",
        )
    )

    assert prepared.duration_seconds == 2.0
    assert [frame.timestamp_seconds for frame in prepared.frames] == [0.0, 2.0]
