import httpx
import pytest

from backend.app.services.ad_research_collector import (
    MetaAdsBridgeAdapter,
    normalize_collector_ad,
)


def test_collector_normalizes_an_active_video_ad() -> None:
    result = normalize_collector_ad(
        {
            "id": "meta-ad-1",
            "status": "ACTIVE",
            "video_urls": ["https://cdn.example/ad.mp4"],
            "media_urls": ["https://cdn.example/cover.jpg"],
            "days_running": 3,
            "body_variants": ["example"],
        }
    )
    assert result["ad_library_id"] == "meta-ad-1"
    assert result["video_url"] == "https://cdn.example/ad.mp4"


@pytest.mark.asyncio
async def test_collector_retries_a_remote_protocol_error_once() -> None:
    class Response:
        def raise_for_status(self) -> None:
            return None

        async def aiter_lines(self):
            yield '{"type":"ad","ad":{"ad_library_id":"meta-ad-1","status":"ACTIVE","video_url":"https://cdn.example/ad.mp4","thumbnail_url":"https://cdn.example/cover.jpg"}}'
            yield '{"type":"done","collected_count":1}'

    class Stream:
        async def __aenter__(self):
            client.attempts += 1
            if client.attempts == 1:
                raise httpx.RemoteProtocolError("peer closed connection")
            return Response()

        async def __aexit__(self, *args) -> None:
            return None

    class Client:
        attempts = 0

        def stream(self, *args, **kwargs):
            return Stream()

    client = Client()
    adapter = MetaAdsBridgeAdapter(base_url="http://collector.test", client=client)  # type: ignore[arg-type]

    ads = await adapter.collect(request_id="request-1", query="Aviator", country="IN", limit=1)

    assert client.attempts == 2
    assert [ad.ad_library_id for ad in ads] == ["meta-ad-1"]