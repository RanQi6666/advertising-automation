from __future__ import annotations

import asyncio
import ipaddress
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from socket import AF_INET, AF_INET6, SOCK_STREAM
from urllib.parse import urljoin, urlparse

import httpx


class SafePublicHTTPError(RuntimeError):
    """Raised when a public HTTP fetch violates safety or transfer constraints."""


class SafePublicHTTPStatusError(SafePublicHTTPError):
    """A non-redirect HTTP response whose status is unsuitable for media download."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"media URL returned HTTP {status_code}")


@dataclass(frozen=True)
class DownloadedFile:
    source_url: str
    final_url: str
    path: Path
    content_type: str | None
    bytes_written: int
    attempts: int = 1


async def download_public_http_file(
    url: str,
    destination: Path,
    *,
    max_bytes: int,
    timeout_seconds: float = 10.0,
    max_redirects: int = 3,
    allow_private_networks: bool = False,
    retry_attempts: int = 0,
    retry_delay_seconds: float = 1.0,
) -> DownloadedFile:
    """Safely download a public HTTP(S) file with SSRF guardrails.

    This is intentionally stricter than a normal HTTP client because callers provide media URLs.
    It accepts public HTTP or HTTPS URLs, validates every redirect target, resolves DNS before
    connecting, blocks private/link-local/loopback IPs by default, and streams to disk with a
    hard byte ceiling.
    """

    total_attempts = max(int(retry_attempts), 0) + 1
    for attempt in range(1, total_attempts + 1):
        try:
            async with asyncio.timeout(timeout_seconds):
                downloaded = await _download_public_http_file(
                    url,
                    destination,
                    max_bytes=max_bytes,
                    timeout_seconds=timeout_seconds,
                    max_redirects=max_redirects,
                    allow_private_networks=allow_private_networks,
                )
            return DownloadedFile(
                source_url=downloaded.source_url,
                final_url=downloaded.final_url,
                path=downloaded.path,
                content_type=downloaded.content_type,
                bytes_written=downloaded.bytes_written,
                attempts=attempt,
            )
        except TimeoutError as exc:
            error: BaseException = SafePublicHTTPError(
                f"media download timed out after {timeout_seconds:g} seconds"
            )
            error.__cause__ = exc
        except BaseException as exc:
            error = exc

        _remove_download_artifacts(destination)
        if attempt >= total_attempts or not is_retryable_media_download_error(error):
            raise error
        await asyncio.sleep(max(float(retry_delay_seconds), 0.0))

    raise SafePublicHTTPError("media download did not complete")  # pragma: no cover


def is_retryable_media_download_error(error: BaseException) -> bool:
    """Return whether an error is likely transient and safe to retry once."""

    for current in _exception_chain(error):
        if isinstance(current, SafePublicHTTPStatusError):
            return current.status_code in {408, 429, 500, 502, 503, 504}
        if isinstance(current, (httpx.TimeoutException, httpx.TransportError, TimeoutError)):
            return True
        if isinstance(current, SafePublicHTTPError) and "timed out" in str(current).lower():
            return True
    return False


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _remove_download_artifacts(destination: Path) -> None:
    destination.with_name(f".{destination.name}.part").unlink(missing_ok=True)
    destination.unlink(missing_ok=True)


async def _download_public_http_file(
    url: str,
    destination: Path,
    *,
    max_bytes: int,
    timeout_seconds: float,
    max_redirects: int,
    allow_private_networks: bool,
) -> DownloadedFile:
    current_url = url.strip()
    final_response: httpx.Response | None = None
    # A media download has one caller-defined end-to-end deadline.  Keeping a
    # much shorter connect/TLS deadline would make a video configured for an
    # 80-second transfer budget fail after five seconds before the transfer
    # even begins, which is especially brittle for external HTTPS storage.
    timeout = httpx.Timeout(timeout_seconds)
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout, trust_env=False
    ) as client:
        for redirect_count in range(max_redirects + 1):
            await _validate_public_http_url(
                current_url,
                allow_private_networks=allow_private_networks,
            )
            request = client.build_request("GET", current_url)
            response = await client.send(request, stream=True)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                await response.aclose()
                if not location:
                    raise SafePublicHTTPError("redirect response missing Location header")
                if redirect_count >= max_redirects:
                    raise SafePublicHTTPError("too many redirects while downloading media")
                current_url = urljoin(current_url, location)
                continue
            final_response = response
            break

        if final_response is None:
            raise SafePublicHTTPError("media download did not produce a response")
        try:
            if final_response.status_code >= 400:
                raise SafePublicHTTPStatusError(final_response.status_code)
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial_path = destination.with_name(f".{destination.name}.part")
            partial_path.unlink(missing_ok=True)
            bytes_written = 0
            try:
                with partial_path.open("wb") as handle:
                    async for chunk in final_response.aiter_bytes():
                        if not chunk:
                            continue
                        bytes_written += len(chunk)
                        if bytes_written > max_bytes:
                            raise SafePublicHTTPError("media file exceeds configured byte limit")
                        handle.write(chunk)
                if bytes_written <= 0:
                    raise SafePublicHTTPError("media URL returned an empty body")
                partial_path.replace(destination)
            except BaseException:
                partial_path.unlink(missing_ok=True)
                raise
            return DownloadedFile(
                source_url=url,
                final_url=str(final_response.url),
                path=destination,
                content_type=final_response.headers.get("content-type"),
                bytes_written=bytes_written,
            )
        finally:
            await final_response.aclose()


async def _validate_public_http_url(
    url: str,
    *,
    allow_private_networks: bool,
) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise SafePublicHTTPError("media URL must be a complete HTTP or HTTPS URL")
    hostname = parsed.hostname.lower()
    default_port = 443 if parsed.scheme == "https" else 80
    await _validate_dns_targets(hostname, parsed.port or default_port, allow_private_networks)


async def _validate_dns_targets(hostname: str, port: int, allow_private_networks: bool) -> None:
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(
            hostname,
            port,
            family=0,
            type=SOCK_STREAM,
            proto=0,
            flags=0,
        )
    except OSError as exc:  # pragma: no cover - platform-specific errno text
        raise SafePublicHTTPError(f"failed to resolve media host: {hostname}") from exc
    if not infos:
        raise SafePublicHTTPError(f"failed to resolve media host: {hostname}")
    for family, _, _, _, sockaddr in infos:
        if family not in {AF_INET, AF_INET6}:
            continue
        ip_text = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_text)
        except ValueError as exc:  # pragma: no cover - getaddrinfo should not return this
            raise SafePublicHTTPError(f"invalid resolved IP for media host: {ip_text}") from exc
        if allow_private_networks:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise SafePublicHTTPError("media host resolves to a non-public IP address")


def extension_from_url_or_content_type(url: str, content_type: str | None, fallback: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    if suffix:
        return suffix
    if content_type:
        guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip())
        if guessed:
            return guessed
    return fallback
