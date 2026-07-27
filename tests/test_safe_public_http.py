import asyncio

import httpx
import pytest

from backend.app.services import safe_public_http
from backend.app.services.safe_public_http import (
    DownloadedFile,
    SafePublicHTTPError,
    SafePublicHTTPStatusError,
    _validate_public_http_url,
    download_public_http_file,
    is_retryable_media_download_error,
)


def test_validate_url_allows_any_public_http_or_https_host(monkeypatch):
    async def fake_validate_dns_targets(*args, **kwargs):
        return None

    monkeypatch.setattr(safe_public_http, "_validate_dns_targets", fake_validate_dns_targets)

    asyncio.run(
        _validate_public_http_url(
            "http://media.other-system.example/uploads/ad.mp4",
            allow_private_networks=False,
        )
    )
    asyncio.run(
        _validate_public_http_url(
            "https://storage.example.net/uploads/ad.mp4",
            allow_private_networks=False,
        )
    )

    with pytest.raises(SafePublicHTTPError, match="complete HTTP or HTTPS URL"):
        asyncio.run(
            _validate_public_http_url(
                "file:///tmp/ad.mp4",
                allow_private_networks=False,
            )
        )


def test_validate_url_blocks_private_resolution_unless_explicitly_enabled(monkeypatch):
    async def fake_getaddrinfo(self, host, port, **kwargs):
        return [(2, 1, 6, "", ("10.20.30.40", port))]

    monkeypatch.setattr(asyncio.BaseEventLoop, "getaddrinfo", fake_getaddrinfo)

    with pytest.raises(SafePublicHTTPError, match="non-public IP"):
        asyncio.run(
            _validate_public_http_url(
                "https://newpixel.messrocts.com/uploads/ad.mp4",
                allow_private_networks=False,
            )
        )

    asyncio.run(
        _validate_public_http_url(
            "https://newpixel.messrocts.com/uploads/ad.mp4",
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

    validated_urls: list[str] = []

    async def fake_validate(url, **kwargs):
        validated_urls.append(url)
        if "attacker.example" in url:
            raise SafePublicHTTPError("media host resolves to a non-public IP address")

    monkeypatch.setattr(safe_public_http, "_validate_public_http_url", fake_validate)

    with pytest.raises(SafePublicHTTPError, match="non-public IP"):
        asyncio.run(
            download_public_http_file(
                "https://newpixel.messrocts.com/uploads/ad.mp4",
                tmp_path / "ad.mp4",
                max_bytes=1024,
            )
        )

    assert len(fake_client.requests) == 1
    assert validated_urls == [
        "https://newpixel.messrocts.com/uploads/ad.mp4",
        "https://attacker.example/stolen.mp4",
    ]


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
        download_public_http_file(
            "https://newpixel.messrocts.com/uploads/ad.mp4",
            destination,
            max_bytes=1024,
        )
    )

    assert destination.read_bytes() == b"complete-media"
    assert result.bytes_written == len(b"complete-media")
    assert result.content_type == "video/mp4"


def test_download_uses_the_full_transfer_deadline_for_connect_and_tls(monkeypatch, tmp_path):
    captured: dict[str, object] = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def build_request(self, method, url):
            return httpx.Request(method, url)

        async def send(self, request, stream=False):
            return httpx.Response(200, content=b"video", request=request)

    def fake_client(**kwargs):
        captured.update(kwargs)
        return FakeClient()

    async def fake_dns(*args, **kwargs):
        return None

    monkeypatch.setattr(safe_public_http.httpx, "AsyncClient", fake_client)
    monkeypatch.setattr(safe_public_http, "_validate_dns_targets", fake_dns)

    asyncio.run(
        download_public_http_file(
            "https://newpixel.messrocts.com/uploads/ad.mp4",
            tmp_path / "ad.mp4",
            max_bytes=1024,
            timeout_seconds=80,
        )
    )

    timeout = captured["timeout"]
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 80
    assert timeout.read == 80
    assert timeout.write == 80
    assert timeout.pool == 80


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
            download_public_http_file(
                "https://newpixel.messrocts.com/uploads/ad.mp4",
                destination,
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
            download_public_http_file(
                "https://newpixel.messrocts.com/uploads/slow.mp4",
                destination,
                max_bytes=1024,
                timeout_seconds=0.01,
            )
        )

    assert not destination.exists()
    assert not (tmp_path / ".slow.mp4.part").exists()


def test_download_retries_a_transient_timeout_once_and_reports_attempt_count(monkeypatch, tmp_path):
    attempts = 0
    destination = tmp_path / "retry.mp4"

    async def fake_download_once(url, destination_path, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            destination_path.parent.mkdir(parents=True, exist_ok=True)
            destination_path.write_bytes(b"stale")
            destination_path.with_name(f".{destination_path.name}.part").write_bytes(b"partial")
            raise SafePublicHTTPError("media download timed out after 30 seconds")
        destination_path.write_bytes(b"complete-media")
        return DownloadedFile(
            source_url=url,
            final_url=url,
            path=destination_path,
            content_type="video/mp4",
            bytes_written=len(b"complete-media"),
        )

    monkeypatch.setattr(safe_public_http, "_download_public_http_file", fake_download_once)

    result = asyncio.run(
        download_public_http_file(
            "https://newpixel.messrocts.com/uploads/retry.mp4",
            destination,
            max_bytes=1024,
            retry_attempts=1,
            retry_delay_seconds=0,
        )
    )

    assert attempts == 2
    assert destination.read_bytes() == b"complete-media"
    assert result.attempts == 2
    assert not (tmp_path / ".retry.mp4.part").exists()


def test_download_does_not_retry_a_deterministic_http_client_error(monkeypatch, tmp_path):
    attempts = 0

    async def fake_download_once(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise SafePublicHTTPStatusError(404)

    monkeypatch.setattr(safe_public_http, "_download_public_http_file", fake_download_once)

    with pytest.raises(SafePublicHTTPStatusError, match="HTTP 404"):
        asyncio.run(
            download_public_http_file(
                "https://newpixel.messrocts.com/uploads/missing.mp4",
                tmp_path / "missing.mp4",
                max_bytes=1024,
                retry_attempts=1,
                retry_delay_seconds=0,
            )
        )

    assert attempts == 1
    assert is_retryable_media_download_error(SafePublicHTTPStatusError(404)) is False
    assert is_retryable_media_download_error(SafePublicHTTPStatusError(503)) is True
