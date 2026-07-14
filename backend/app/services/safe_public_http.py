from __future__ import annotations

import asyncio
import ipaddress
import mimetypes
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from socket import AF_INET, AF_INET6, SOCK_STREAM
from urllib.parse import urljoin, urlparse

import httpx


class SafePublicHTTPError(RuntimeError):
    """Raised when a public HTTP fetch violates safety or transfer constraints."""


@dataclass(frozen=True)
class DownloadedFile:
    source_url: str
    final_url: str
    path: Path
    content_type: str | None
    bytes_written: int


def normalize_allowed_hosts(hosts: Iterable[str]) -> set[str]:
    return {host.strip().lower() for host in hosts if host and host.strip()}


async def download_public_https_file(
    url: str,
    destination: Path,
    *,
    allowed_hosts: Iterable[str],
    max_bytes: int,
    timeout_seconds: float = 10.0,
    max_redirects: int = 3,
    allow_private_networks: bool = False,
) -> DownloadedFile:
    """Safely download a public HTTPS file with host allow-list and SSRF guardrails.

    This is intentionally stricter than a normal HTTP client because callers provide media URLs.
    It allows only HTTPS, validates every redirect target, resolves DNS before connecting, blocks
    private/link-local/loopback IPs by default, and streams to disk with a hard byte ceiling.
    """

    partial_path = destination.with_name(f".{destination.name}.part")
    try:
        async with asyncio.timeout(timeout_seconds):
            return await _download_public_https_file(
                url,
                destination,
                allowed_hosts=allowed_hosts,
                max_bytes=max_bytes,
                timeout_seconds=timeout_seconds,
                max_redirects=max_redirects,
                allow_private_networks=allow_private_networks,
            )
    except TimeoutError as exc:
        partial_path.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        raise SafePublicHTTPError(
            f"media download timed out after {timeout_seconds:g} seconds"
        ) from exc


async def _download_public_https_file(
    url: str,
    destination: Path,
    *,
    allowed_hosts: Iterable[str],
    max_bytes: int,
    timeout_seconds: float,
    max_redirects: int,
    allow_private_networks: bool,
) -> DownloadedFile:
    allowed = normalize_allowed_hosts(allowed_hosts)
    if not allowed:
        raise SafePublicHTTPError("no allowed media hosts configured")

    current_url = url.strip()
    final_response: httpx.Response | None = None
    timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 5.0))
    async with httpx.AsyncClient(
        follow_redirects=False, timeout=timeout, trust_env=False
    ) as client:
        for redirect_count in range(max_redirects + 1):
            await _validate_public_https_url(
                current_url,
                allowed_hosts=allowed,
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
                raise SafePublicHTTPError(f"media URL returned HTTP {final_response.status_code}")
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


async def _validate_public_https_url(
    url: str,
    *,
    allowed_hosts: set[str],
    allow_private_networks: bool,
) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise SafePublicHTTPError("media URL must be a complete HTTPS URL")
    hostname = parsed.hostname.lower()
    if hostname not in allowed_hosts:
        raise SafePublicHTTPError(f"media host is not allowed: {hostname}")
    await _validate_dns_targets(hostname, parsed.port or 443, allow_private_networks)


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
