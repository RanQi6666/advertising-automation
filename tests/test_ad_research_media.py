import asyncio

import pytest

from backend.app.schemas.ad_research import CollectorAd
from backend.app.services.ad_research_media import AdResearchMediaInspector


@pytest.mark.asyncio
async def test_media_inspector_rejects_link_local_video_url_when_duration_is_reported(
) -> None:
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
