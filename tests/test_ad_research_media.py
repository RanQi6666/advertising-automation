import asyncio

import pytest

from backend.app.schemas.ad_research import CollectorAd
from backend.app.core.config import get_settings
from backend.app.services.ad_research_media import (
    AdResearchMediaInspector,
    PreparedAdMedia,
    TechnicalQualification,
)


@pytest.fixture
def allow_public_media(monkeypatch):
    import backend.app.services.ad_research_media as media_module

    async def allow_public_url(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(media_module, "validate_public_http_url", allow_public_url)


def eligible_ad(**overrides) -> CollectorAd:
    values = {
        "ad_library_id": "eligible",
        "status": "ACTIVE",
        "days_running": 1,
        "video_url": "https://cdn.example/ad.mp4",
        "thumbnail_url": "https://cdn.example/ad.jpg",
        "duration_seconds": 30,
    }
    values.update(overrides)
    return CollectorAd(**values)


@pytest.mark.asyncio
@pytest.mark.parametrize("days_running", [None, 0])
async def test_media_inspector_rejects_ads_active_for_less_than_one_day(
    allow_public_media, days_running
) -> None:
    result = await AdResearchMediaInspector().inspect(eligible_ad(days_running=days_running))

    assert result.qualified is False
    assert "active_days_below_minimum" in result.reasons
    assert result.active_days == days_running


@pytest.mark.asyncio
async def test_media_inspector_accepts_one_active_day(allow_public_media) -> None:
    result = await AdResearchMediaInspector().inspect(eligible_ad(days_running=1))

    assert result.qualified is True
    assert result.reasons == ()


@pytest.mark.asyncio
async def test_media_inspector_accepts_exactly_thirty_seconds(allow_public_media) -> None:
    result = await AdResearchMediaInspector().inspect(eligible_ad(duration_seconds=30))

    assert result.qualified is True
    assert result.duration_seconds == 30


@pytest.mark.asyncio
async def test_media_inspector_rejects_video_over_thirty_seconds(
    allow_public_media,
) -> None:
    result = await AdResearchMediaInspector().inspect(eligible_ad(duration_seconds=30.1))

    assert result.qualified is False
    assert "duration_over_30" in result.reasons
    assert result.duration_seconds == 30.1


@pytest.mark.asyncio
async def test_media_inspector_records_multiple_rejection_reasons(
    allow_public_media,
) -> None:
    result = await AdResearchMediaInspector().inspect(
        eligible_ad(status="INACTIVE", days_running=0, thumbnail_url=None, duration_seconds=31)
    )

    assert result.qualified is False
    assert result.reasons == (
        "status_not_active",
        "missing_thumbnail",
        "active_days_below_minimum",
        "duration_over_30",
    )


@pytest.mark.asyncio
async def test_media_inspector_rejects_link_local_video_url_when_duration_is_reported() -> None:
    ad = CollectorAd(
        ad_library_id="unsafe",
        status="ACTIVE",
        days_running=3,
        video_url="http://169.254.169.254/latest/meta-data/video.mp4",
        thumbnail_url="https://cdn.example/ad.jpg",
        duration_seconds=15,
    )

    assert await AdResearchMediaInspector().is_technically_qualified(ad) is False


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

    assert await AdResearchMediaInspector().probe_duration("https://cdn.example/ad.mp4") is None
    assert process.killed is True
    assert process.waited is True


def test_ad_research_media_settings_have_safe_parallel_defaults(monkeypatch) -> None:
    monkeypatch.delenv("AD_RESEARCH_MEDIA_CONCURRENCY", raising=False)
    monkeypatch.delenv("AD_RESEARCH_FRAME_CONCURRENCY", raising=False)
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.ad_research_media_concurrency == 6
    assert settings.ad_research_frame_concurrency == 4
    get_settings.cache_clear()


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
