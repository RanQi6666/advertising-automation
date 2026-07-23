from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from backend.app.core.config import get_settings
from backend.app.schemas.ad_research import CollectorAd
from backend.app.services.ad_research_media import (
    AdResearchMediaInspector,
    PreparedAdMedia,
    TechnicalQualification,
)


def eligible_ad(**overrides) -> CollectorAd:
    values = {
        "ad_library_id": "eligible",
        "status": "ACTIVE",
        "days_running": 1,
        "video_url": "https://cdn.example/ad.mp4",
        "thumbnail_url": "https://cdn.example/ad.jpg",
        "duration_seconds": 20.0,
    }
    values.update(overrides)
    return CollectorAd(**values)


@pytest.fixture
def inspector_with_storage(tmp_path, monkeypatch) -> AdResearchMediaInspector:
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("AD_RESEARCH_MEDIA_ROOT", str(tmp_path / "storage" / "ad-research"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ai.example")
    get_settings.cache_clear()
    return AdResearchMediaInspector()


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def allow_public_media(monkeypatch):
    import backend.app.services.ad_research_media as media_module

    async def allow_public_url(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(media_module, "validate_public_http_url", allow_public_url)


async def _write_downloaded_video(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"video")
    return destination


async def _write_frame(destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"frame")
    return destination


@pytest.mark.asyncio
async def test_missing_thumbnail_uses_generated_cover_from_video(
    inspector_with_storage, allow_public_media, monkeypatch
) -> None:
    inspector = inspector_with_storage
    monkeypatch.setattr(inspector, "_download_video", lambda *_: _write_downloaded_video(_[1]))
    monkeypatch.setattr(inspector, "_extract_frame", lambda *args: _write_frame(args[1]))

    qualification = await inspector.inspect(eligible_ad(thumbnail_url=None), job_id="job-1")

    assert qualification.qualified is True
    assert qualification.media is not None
    assert qualification.media.cover_source == "generated_frame"
    assert len(qualification.media.frame_urls) == 3
    assert qualification.media.cover_url and qualification.media.cover_url.endswith("/cover.jpg")


@pytest.mark.asyncio
async def test_duration_uses_downloaded_ffprobe_after_three_remote_failures(
    inspector_with_storage, allow_public_media, monkeypatch
) -> None:
    inspector = inspector_with_storage
    remote_attempts = 0

    async def no_remote_duration(*args, **kwargs) -> None:
        nonlocal remote_attempts
        remote_attempts += 1
        return None

    async def local_duration(*args, **kwargs) -> float:
        return 18.0

    monkeypatch.setattr(inspector, "probe_remote_duration", no_remote_duration)
    monkeypatch.setattr(inspector, "_download_video", lambda *_: _write_downloaded_video(_[1]))
    monkeypatch.setattr(inspector, "_probe_local_video", local_duration)
    monkeypatch.setattr(inspector, "_extract_frame", lambda *args: _write_frame(args[1]))

    qualification = await inspector.inspect(
        eligible_ad(duration_seconds=None, thumbnail_url=None), job_id="job-1"
    )

    assert qualification.qualified is True
    assert qualification.duration_seconds == 18.0
    assert qualification.media is not None
    assert qualification.media.duration_source == "downloaded_ffprobe"
    assert qualification.media.duration_probe_attempts == 3
    assert remote_attempts == 3


@pytest.mark.asyncio
async def test_inspect_many_never_exceeds_media_concurrency(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AD_RESEARCH_MEDIA_CONCURRENCY", "2")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("AD_RESEARCH_MEDIA_ROOT", str(tmp_path / "storage" / "ad-research"))
    get_settings.cache_clear()

    class DelayedInspector(AdResearchMediaInspector):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.max_active_media_operations = 0

        async def inspect(self, ad: CollectorAd, *, job_id: str) -> TechnicalQualification:
            self.active += 1
            self.max_active_media_operations = max(self.max_active_media_operations, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return TechnicalQualification(True, (), 10.0, 1)

    inspector = DelayedInspector()
    results = await inspector.inspect_many(
        [eligible_ad(ad_library_id=str(index)) for index in range(5)], job_id="job-1"
    )

    assert len(results) == 5
    assert inspector.max_active_media_operations <= 2


@pytest.mark.asyncio
@pytest.mark.parametrize("days_running", [None, 0])
async def test_media_inspector_rejects_ads_active_for_less_than_one_day(
    inspector_with_storage, allow_public_media, days_running
) -> None:
    result = await inspector_with_storage.inspect(
        eligible_ad(days_running=days_running), job_id="job-1"
    )

    assert result.qualified is False
    assert "active_days_below_minimum" in result.reasons
    assert result.active_days == days_running


@pytest.mark.asyncio
async def test_media_inspector_rejects_video_over_thirty_seconds(
    inspector_with_storage, allow_public_media
) -> None:
    result = await inspector_with_storage.inspect(
        eligible_ad(duration_seconds=30.1), job_id="job-1"
    )

    assert result.qualified is False
    assert "duration_over_30" in result.reasons
    assert result.duration_seconds == 30.1


@pytest.mark.asyncio
async def test_media_inspector_rejects_unknown_duration_after_fallback(
    inspector_with_storage, allow_public_media, monkeypatch
) -> None:
    inspector = inspector_with_storage

    async def no_duration(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(inspector, "probe_remote_duration", no_duration)
    monkeypatch.setattr(inspector, "_download_video", lambda *_: _write_downloaded_video(_[1]))
    monkeypatch.setattr(inspector, "_probe_local_video", no_duration)

    result = await inspector.inspect(eligible_ad(duration_seconds=None), job_id="job-1")

    assert result.qualified is False
    assert result.reasons == ("duration_unavailable",)


@pytest.mark.asyncio
async def test_media_inspector_rejects_when_all_frames_fail(
    inspector_with_storage, allow_public_media, monkeypatch
) -> None:
    inspector = inspector_with_storage

    async def no_frame(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(inspector, "_download_video", lambda *_: _write_downloaded_video(_[1]))
    monkeypatch.setattr(inspector, "_extract_frame", no_frame)

    result = await inspector.inspect(eligible_ad(thumbnail_url=None), job_id="job-1")

    assert result.qualified is False
    assert result.reasons == ("no_analyzable_visual",)


@pytest.mark.asyncio
async def test_retain_only_removes_unselected_ad_artifacts(inspector_with_storage) -> None:
    root = inspector_with_storage._artifact_dir("job-1", "keep").parent
    for name in ("keep", "drop"):
        child = root / name
        child.mkdir(parents=True, exist_ok=True)
        (child / "frame.jpg").write_bytes(b"frame")

    await inspector_with_storage.retain_only("job-1", {"keep"})

    assert (root / "keep" / "frame.jpg").exists()
    assert not (root / "drop").exists()


@pytest.mark.asyncio
async def test_cleanup_job_media_is_idempotent(inspector_with_storage) -> None:
    root = inspector_with_storage._artifact_dir("job-1", "ad-1").parent
    (root / "ad-1").mkdir(parents=True, exist_ok=True)

    await inspector_with_storage.cleanup_job_media("job-1")
    await inspector_with_storage.cleanup_job_media("job-1")

    assert not root.exists()


@pytest.mark.asyncio
async def test_media_inspector_rejects_link_local_video_url_when_duration_is_reported() -> None:
    ad = eligible_ad(video_url="http://169.254.169.254/latest/meta-data/video.mp4")

    result = await AdResearchMediaInspector().inspect(ad, job_id="job-1")

    assert result.qualified is False
    assert "video_url_rejected" in result.reasons


@pytest.mark.asyncio
async def test_media_inspector_kills_ffprobe_after_timeout(monkeypatch) -> None:
    import backend.app.services.ad_research_media as media_module

    class Process:
        returncode = None

        def __init__(self) -> None:
            self.killed = False
            self.waited = False

        async def communicate(self):
            await asyncio.sleep(60)
            return b"", b""

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> None:
            self.waited = True

    process = Process()

    async def timeout(awaitable, timeout):
        awaitable.close()
        raise TimeoutError

    async def create_process(*args, **kwargs):
        return process

    async def allow_public_url(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(media_module.asyncio, "wait_for", timeout)
    monkeypatch.setattr(media_module.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(media_module, "validate_public_http_url", allow_public_url)

    assert (
        await AdResearchMediaInspector().probe_remote_duration("https://cdn.example/ad.mp4") is None
    )
    assert process.killed is True
    assert process.waited is True


def test_ad_research_media_settings_have_safe_parallel_defaults(monkeypatch) -> None:
    monkeypatch.delenv("AD_RESEARCH_MEDIA_CONCURRENCY", raising=False)
    monkeypatch.delenv("AD_RESEARCH_FRAME_CONCURRENCY", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.ad_research_media_concurrency == 6
    assert settings.ad_research_frame_concurrency == 4


def test_technical_qualification_can_hold_worker_only_media_paths(tmp_path) -> None:
    media = PreparedAdMedia(
        cover_url="https://ai.example/storage/ad-research/job/ad/cover.jpg",
        cover_source="generated_frame",
        frame_urls=("https://ai.example/storage/ad-research/job/ad/frame_20.jpg",),
        local_frame_paths=(tmp_path / "frame_20.jpg",),
        duration_source="collector",
        duration_probe_attempts=0,
    )
    result = TechnicalQualification(True, (), 12.0, 3, media)

    assert result.media is media
    assert result.media.local_frame_paths[0].name == "frame_20.jpg"
