import asyncio
import tomllib
from pathlib import Path

import httpx
import pytest

from backend.app.core.config import get_settings
from backend.app.services.ad_analysis_media_service import (
    AdAnalysisMediaService,
    VideoFrameExtractionResult,
    _extract_video_keyframes,
    _generate_image_thumbnail,
    _probe_video,
    _select_distinct_middle_frames,
    _warning_from_exception,
)
from backend.app.services.safe_public_http import DownloadedFile, SafePublicHTTPError


def test_media_runtime_dependencies_declared_for_complete_processing():
    """The complete external ad-analysis path must be able to derive media artifacts."""

    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = [dependency.lower() for dependency in pyproject["project"]["dependencies"]]
    assert any(dependency.startswith("pillow") for dependency in dependencies)

    dockerfile = Path("infra/docker/backend.Dockerfile").read_text(encoding="utf-8").lower()
    assert "apt-get install" in dockerfile
    assert "ffmpeg" in dockerfile


def test_config_blocks_private_media_resolution_by_default(monkeypatch):
    monkeypatch.delenv("AD_ANALYSIS_ALLOW_PRIVATE_MEDIA_HOSTS", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.ad_analysis_allow_private_media_hosts is False

    get_settings.cache_clear()


def test_probe_video_fails_closed_when_ffprobe_is_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service.shutil.which",
        lambda _name: None,
    )

    with pytest.raises(SafePublicHTTPError, match="ffprobe is unavailable"):
        asyncio.run(_probe_video(tmp_path / "disguised.mp4"))


def test_probe_video_timeout_kills_subprocess(monkeypatch, tmp_path):
    monkeypatch.setenv("AD_ANALYSIS_FFPROBE_TIMEOUT_SECONDS", "0.01")
    get_settings.cache_clear()
    state = {"killed": False, "waited": False}

    class FakeProcess:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(0.05)
            return b"", b""

        def kill(self):
            state["killed"] = True

        async def wait(self):
            state["waited"] = True
            self.returncode = -9

    async def fake_subprocess(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service.shutil.which",
        lambda _name: "ffprobe",
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

    with pytest.raises(SafePublicHTTPError, match="ffprobe timed out"):
        asyncio.run(_probe_video(tmp_path / "video.mp4"))

    assert state == {"killed": True, "waited": True}
    get_settings.cache_clear()


def test_keyframe_timeout_kills_ffmpeg_and_returns_incomplete_frame_result(monkeypatch, tmp_path):
    monkeypatch.setenv("AD_ANALYSIS_FFMPEG_FRAME_TIMEOUT_SECONDS", "0.01")
    get_settings.cache_clear()
    state = {"kills": 0, "waits": 0}

    class FakeProcess:
        returncode = None

        async def communicate(self):
            await asyncio.sleep(0.05)
            return b"", b""

        def kill(self):
            state["kills"] += 1

        async def wait(self):
            state["waits"] += 1
            self.returncode = -9

    async def fake_subprocess(*_args, **_kwargs):
        return FakeProcess()

    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service.shutil.which",
        lambda _name: "ffmpeg",
    )
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)

    result = asyncio.run(_extract_video_keyframes(tmp_path / "video.mp4", tmp_path, 12.0))

    assert state == {"kills": 7, "waits": 7}
    assert result.first_frame_path is None
    assert result.last_frame_path is None
    assert any("timed out" in warning for warning in result.warnings)
    get_settings.cache_clear()


def test_middle_frame_selection_supports_async_frame_comparisons(monkeypatch, tmp_path):
    first = tmp_path / "first.jpg"
    last = tmp_path / "last.jpg"
    candidates = [tmp_path / f"candidate-{index}.jpg" for index in range(1, 5)]
    for path in [first, last, *candidates]:
        path.write_bytes(path.name.encode())

    comparisons: list[tuple[Path, Path]] = []

    async def fake_distinct(candidate: Path, reference: Path) -> bool:
        comparisons.append((candidate, reference))
        return True

    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service._video_frames_are_distinct",
        fake_distinct,
    )

    selected = asyncio.run(_select_distinct_middle_frames(candidates, first, last))

    assert selected == candidates[:3]
    assert comparisons


def test_generate_image_thumbnail_creates_jpeg_artifact(tmp_path):
    from PIL import Image

    source = tmp_path / "source.png"
    destination = tmp_path / "thumbnail.jpg"
    Image.new("RGBA", (1024, 768), (255, 30, 30, 180)).save(source)

    generated = asyncio.run(_generate_image_thumbnail(source, destination))

    assert generated is True
    assert destination.exists()
    assert destination.stat().st_size > 0
    with Image.open(destination) as thumbnail:
        assert thumbnail.format == "JPEG"
        assert thumbnail.size[0] <= 512
        assert thumbnail.size[1] <= 512


def test_media_warning_preserves_nested_connection_failure_reason():
    request = httpx.Request("GET", "https://newpixel.messrocts.com/uploads/ad.mp4")
    try:
        try:
            raise OSError("TLS handshake closed unexpectedly")
        except OSError as exc:
            raise httpx.ConnectError("", request=request) from exc
    except httpx.ConnectError as exc:
        warning = _warning_from_exception(exc)

    assert "ConnectError" in warning
    assert "TLS handshake closed unexpectedly" in warning


def test_video_longer_than_configured_limit_degrades_media_analysis(monkeypatch, tmp_path):
    monkeypatch.setenv("AD_ANALYSIS_MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("AD_ANALYSIS_VIDEO_MAX_DURATION_SECONDS", "20")
    get_settings.cache_clear()

    async def fake_download(url, destination, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-video")
        return DownloadedFile(
            source_url=url,
            final_url=url,
            path=destination,
            content_type="video/mp4",
            bytes_written=10,
        )

    async def fake_probe(_source):
        return {
            "duration_seconds": 20.01,
            "format": "mov,mp4,m4a,3gp,3g2,mj2",
            "codec": "h264",
        }

    extraction = {"called": False}

    async def should_not_extract(*_args, **_kwargs):
        extraction["called"] = True
        return []

    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service.download_public_http_file",
        fake_download,
    )
    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service._probe_video",
        fake_probe,
    )
    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service._extract_video_keyframes",
        should_not_extract,
    )

    result = asyncio.run(
        AdAnalysisMediaService().process_media(
            {
                "creative": {
                    "creative_type": "video",
                    "video_url": "https://newpixel.messrocts.com/uploads/too-long.mp4",
                }
            },
            analysis_id="ana-too-long",
        )
    )

    assert result.summary["status"] == "unavailable"
    assert "exceeds configured 20s limit" in result.summary["warnings"][0]
    assert extraction["called"] is False
    get_settings.cache_clear()


def test_unrecognized_ffprobe_format_degrades_media_analysis(monkeypatch, tmp_path):
    monkeypatch.setenv("AD_ANALYSIS_MEDIA_ROOT", str(tmp_path / "media"))
    get_settings.cache_clear()

    async def fake_download(url, destination, **kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-video")
        return DownloadedFile(
            source_url=url,
            final_url=url,
            path=destination,
            content_type="application/octet-stream",
            bytes_written=10,
        )

    async def fake_probe(_source):
        return {"duration_seconds": 12.0, "format": "avi", "codec": "mpeg4"}

    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service.download_public_http_file",
        fake_download,
    )
    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service._probe_video",
        fake_probe,
    )

    result = asyncio.run(
        AdAnalysisMediaService().process_media(
            {
                "creative": {
                    "creative_type": "video",
                    "video_url": "https://newpixel.messrocts.com/uploads/disguised.mp4",
                }
            },
            analysis_id="ana-bad-format",
        )
    )

    assert result.summary["status"] == "unavailable"
    assert "MP4, MOV, or WebM" in result.summary["warnings"][0]
    get_settings.cache_clear()


def test_carousel_downloads_and_derives_a_thumbnail_for_every_card(monkeypatch, tmp_path):
    monkeypatch.setenv("AD_ANALYSIS_MEDIA_ROOT", str(tmp_path / "media"))
    get_settings.cache_clear()
    downloaded_urls: list[str] = []

    async def fake_download(url, destination, **kwargs):
        from PIL import Image

        downloaded_urls.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 480), "red").save(destination, format="JPEG")
        return DownloadedFile(
            source_url=url,
            final_url=url,
            path=destination,
            content_type="image/jpeg",
            bytes_written=destination.stat().st_size,
        )

    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service.download_public_http_file",
        fake_download,
    )

    result = asyncio.run(
        AdAnalysisMediaService().process_media(
            {
                "creative": {
                    "creative_type": "carousel",
                    "image_urls": [
                        "https://newpixel.messrocts.com/uploads/cards/one.jpg",
                        "https://newpixel.messrocts.com/uploads/cards/two.jpg",
                    ],
                }
            },
            analysis_id="ana-carousel",
        )
    )

    assert result.summary["status"] == "available"
    assert downloaded_urls == [
        "https://newpixel.messrocts.com/uploads/cards/one.jpg",
        "https://newpixel.messrocts.com/uploads/cards/two.jpg",
    ]
    assert result.summary["thumbnail_generated"] is True
    paths = result.summary["local_artifacts"]["carousel_thumbnail_paths"]
    assert len(paths) == 2
    assert all(Path(path).exists() for path in paths)
    get_settings.cache_clear()


def test_video_media_uses_80_second_timeout_and_one_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("AD_ANALYSIS_MEDIA_ROOT", str(tmp_path / "media"))
    get_settings.cache_clear()
    download_options: dict[str, object] = {}

    async def fake_download(url, destination, **kwargs):
        download_options.update(kwargs)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-video")
        return DownloadedFile(
            source_url=url,
            final_url=url,
            path=destination,
            content_type="video/mp4",
            bytes_written=10,
            attempts=2,
        )

    async def fake_probe(_source):
        return {"duration_seconds": 12.0, "format": "mov,mp4,m4a,3gp,3g2,mj2", "codec": "h264"}

    async def fake_extract(_source, working_dir, _duration):
        first = working_dir / "first_frame.jpg"
        last = working_dir / "last_frame.jpg"
        first.write_bytes(b"first")
        last.write_bytes(b"last")
        return VideoFrameExtractionResult(first, [], last, [])

    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service.download_public_http_file", fake_download
    )
    monkeypatch.setattr("backend.app.services.ad_analysis_media_service._probe_video", fake_probe)
    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service._extract_video_keyframes", fake_extract
    )

    result = asyncio.run(
        AdAnalysisMediaService().process_media(
            {"creative": {"creative_type": "video", "video_url": "https://newpixel.messrocts.com/video.mp4"}},
            analysis_id="ana-timeout",
        )
    )

    assert download_options["timeout_seconds"] == 80.0
    assert download_options["retry_attempts"] == 1
    assert result.summary["download"]["attempts"] == 2
    assert result.summary["download"]["retry_used"] is True
    get_settings.cache_clear()


def test_video_media_requires_first_and_last_frame_for_complete_visual_status(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("AD_ANALYSIS_MEDIA_ROOT", str(tmp_path / "media"))
    get_settings.cache_clear()

    async def fake_download(url, destination, **_kwargs):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-video")
        return DownloadedFile(url, url, destination, "video/mp4", 10)

    async def fake_probe(_source):
        return {"duration_seconds": 12.0, "format": "mov,mp4,m4a,3gp,3g2,mj2", "codec": "h264"}

    async def fake_only_first(_source, working_dir, _duration):
        first = working_dir / "first_frame.jpg"
        first.write_bytes(b"first")
        return VideoFrameExtractionResult(first, [], None, ["Ending frame could not be extracted."])

    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service.download_public_http_file", fake_download
    )
    monkeypatch.setattr("backend.app.services.ad_analysis_media_service._probe_video", fake_probe)
    monkeypatch.setattr(
        "backend.app.services.ad_analysis_media_service._extract_video_keyframes", fake_only_first
    )

    result = asyncio.run(
        AdAnalysisMediaService().process_media(
            {"creative": {"creative_type": "video", "video_url": "https://newpixel.messrocts.com/video.mp4"}},
            analysis_id="ana-boundary",
        )
    )

    assert result.summary["status"] == "partial"
    assert result.summary["first_frame_generated"] is True
    assert result.summary["last_frame_generated"] is False
    assert result.summary["video_visual_complete"] is False
    assert "incomplete" in result.summary["warnings"][0].lower()
    get_settings.cache_clear()
