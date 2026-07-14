import asyncio

import httpx
import pytest

from backend.app.services import safe_public_http
from backend.app.services.safe_public_http import (
    SafePublicHTTPError,
    _validate_public_https_url,
    download_public_https_file,
)


def test_validate_url_requires_https_and_allowlisted_host(monkeypatch):
    async def fake_validate_dns_targets(*args, **kwargs):
        raise AssertionError("DNS must not run for an invalid URL")

    monkeypatch.setattr(safe_public_http, "_validate_dns_targets", fake_validate_dns_targets)

    with pytest.raises(SafePublicHTTPError, match="complete HTTPS URL"):
        asyncio.run(
            _validate_public_https_url(
                "http://newpixel.messrocts.com/uploads/ad.mp4",
                allowed_hosts={"newpixel.messrocts.com"},
                allow_private_networks=False,
            )
        )

    with pytest.raises(SafePublicHTTPError, match="host is not allowed"):
        asyncio.run(
            _validate_public_https_url(
                "https://attacker.example/uploads/ad.mp4",
                allowed_hosts={"newpixel.messrocts.com"},
                allow_private_networks=False,
            )
        )


def test_validate_url_blocks_private_resolution_unless_explicitly_enabled(monkeypatch):
    async def fake_getaddrinfo(self, host, port, **kwargs):
        return [(2, 1, 6, "", ("10.20.30.40", port))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(SafePublicHTTPError, match="non-public IP"):
        asyncio.run(
            _validate_public_https_url(
                "https://newpixel.messrocts.com/uploads/ad.mp4",
                allowed_hosts={"newpixel.messrocts.com"},
                allow_private_networks=False,
            )
        )

    asyncio.run(
        _validate_public_https_url(
            "https://newpixel.messrocts.com/uploads/ad.mp4",
            allowed_hosts={"newpixel.messrocts.com"},
            allow_private_networks=True,
        )
    )


def test_download_revalidates_redirect_target_before_following(monkeypatch, tmp_path):
    class FakeClient:
        def __init__(self, **kwargs):
            self.requests = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def build_request(self, method, url):
            return httpx.Request(method, url)

        async def send(self, request, stream=False):
            self.requests.append(request)
            return httpx.Response(
                302,
                headers={"location": "https://attacker.example/stolen.mp4"},
                request=request,
            )

    fake_client = FakeClient()
    monkeypatch.setattr(safe_public_http.httpx, "AsyncClient", lambda **kwargs: fake_client)

    async def fake_dns(*args, **kwargs):
        return None

    monkeypatch.setattr(safe_public_http, "_validate_dns_targets", fake_dns)

    with pytest.raises(SafePublicHTTPError, match="host is not allowed"):
        asyncio.run(
            download_public_https_file(
                "https://newpixel.messrocts.com/uploads/ad.mp4",
                tmp_path / "ad.mp4",
                allowed_hosts=["newpixel.messrocts.com"],
                max_bytes=1024,
            )
        )

    assert len(fake_client.requests) == 1


def test_download_supports_httpx_028_response_lifecycle(monkeypatch, tmp_path):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def build_request(self, method, url):
            return httpx.Request(method, url)

        async def send(self, request, stream=False):
            return httpx.Response(
                200,
                headers={"content-type": "video/mp4"},
                content=b"complete-media",
                request=request,
            )

    monkeypatch.setattr(safe_public_http.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    async def fake_dns(*args, **kwargs):
        return None

    monkeypatch.setattr(safe_public_http, "_validate_dns_targets", fake_dns)
    destination = tmp_path / "complete.mp4"

    result = asyncio.run(
        download_public_https_file(
            "https://newpixel.messrocts.com/uploads/ad.mp4",
            destination,
            allowed_hosts=["newpixel.messrocts.com"],
            max_bytes=1024,
        )
    )

    assert destination.read_bytes() == b"complete-media"
    assert result.bytes_written == len(b"complete-media")
    assert result.content_type == "video/mp4"


def test_download_removes_partial_file_when_size_limit_is_exceeded(monkeypatch, tmp_path):
    class FakeStreamingResponse:
        status_code = 200
        headers = {}
        url = "https://newpixel.messrocts.com/uploads/ad.mp4"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_bytes(self):
            yield b"0123456789"

        async def aclose(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def build_request(self, method, url):
            return httpx.Request(method, url)

        async def send(self, request, stream=False):
            return FakeStreamingResponse()

    monkeypatch.setattr(safe_public_http.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    async def fake_dns(*args, **kwargs):
        return None

    monkeypatch.setattr(safe_public_http, "_validate_dns_targets", fake_dns)
    destination = tmp_path / "too-large.mp4"

    with pytest.raises(SafePublicHTTPError, match="byte limit"):
        asyncio.run(
            download_public_https_file(
                "https://newpixel.messrocts.com/uploads/ad.mp4",
                destination,
                allowed_hosts=["newpixel.messrocts.com"],
                max_bytes=5,
            )
        )

    assert not destination.exists()
    assert not (tmp_path / ".too-large.mp4.part").exists()


def test_download_enforces_total_deadline_and_removes_partial_file(monkeypatch, tmp_path):
    class FakeStreamingResponse:
        status_code = 200
        headers = {}
        url = "https://newpixel.messrocts.com/uploads/slow.mp4"

        async def aiter_bytes(self):
            yield b"partial"
            await asyncio.sleep(0.05)
            yield b"late"

        async def aclose(self):
            return None

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def build_request(self, method, url):
            return httpx.Request(method, url)

        async def send(self, request, stream=False):
            return FakeStreamingResponse()

    monkeypatch.setattr(safe_public_http.httpx, "AsyncClient", lambda **kwargs: FakeClient())

    async def fake_dns(*args, **kwargs):
        return None

    monkeypatch.setattr(safe_public_http, "_validate_dns_targets", fake_dns)
    destination = tmp_path / "slow.mp4"

    with pytest.raises(SafePublicHTTPError, match="download timed out"):
        asyncio.run(
            download_public_https_file(
                "https://newpixel.messrocts.com/uploads/slow.mp4",
                destination,
                allowed_hosts=["newpixel.messrocts.com"],
                max_bytes=1024,
                timeout_seconds=0.01,
            )
        )

    assert not destination.exists()
    assert not (tmp_path / ".slow.mp4.part").exists()
