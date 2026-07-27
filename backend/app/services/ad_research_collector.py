from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from backend.app.core.config import get_settings
from backend.app.core.errors import AppError
from backend.app.schemas.ad_research import CollectorAd


class CollectorUnavailable(AppError):
    pass


class AdSourceAdapter(Protocol):
    async def collect(
        self, *, request_id: str, query: str, country: str, limit: int
    ) -> list[CollectorAd]: ...


def normalize_collector_ad(event: dict) -> dict:
    """Map the Bridge/upstream naming to the only candidate shape used by the API."""
    video_urls = event.get("video_urls") or []
    media_urls = event.get("media_urls") or []
    text_variants = event.get("text_variants") or event.get("body_variants") or []
    return {
        "ad_library_id": str(event.get("ad_library_id") or event.get("id") or ""),
        "advertiser_name": _optional_text(event.get("advertiser_name")),
        "status": _optional_text(event.get("status")),
        "days_running": _int_or_none(event.get("days_running")),
        "text_variants": [str(item) for item in text_variants if str(item).strip()],
        "headline": _optional_text(event.get("headline")),
        "cta_text": _optional_text(event.get("cta_text")),
        "landing_url": _optional_text(event.get("landing_url") or event.get("link_url")),
        "video_url": _optional_text(event.get("video_url") or _first(video_urls)),
        "thumbnail_url": _optional_text(event.get("thumbnail_url") or _first(media_urls)),
        "duration_seconds": _float_or_none(event.get("duration_seconds")),
        "platforms": [str(item) for item in (event.get("platforms") or [])],
        "ad_snapshot_url": _optional_text(event.get("ad_snapshot_url")),
        "reported_spend_range": event.get("reported_spend_range"),
    }


class MetaAdsBridgeAdapter:
    """Internal-only JSON-lines client for the pinned meta-ads-scraper bridge."""

    _REMOTE_PROTOCOL_ATTEMPTS = 2

    def __init__(
        self, base_url: str | None = None, client: httpx.AsyncClient | None = None
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.ad_research_collector_base_url).rstrip("/")
        self.timeout_seconds = settings.ad_research_collector_timeout_seconds
        self._client = client

    async def collect(
        self, *, request_id: str, query: str, country: str, limit: int
    ) -> list[CollectorAd]:
        if not self.base_url:
            raise CollectorUnavailable("ad research collector bridge is not configured")
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient()
        try:
            for attempt in range(self._REMOTE_PROTOCOL_ATTEMPTS):
                ads: list[CollectorAd] = []
                try:
                    async with client.stream(
                        "POST",
                        f"{self.base_url}/collect",
                        json={
                            "request_id": request_id,
                            "query": query,
                            "country": country,
                            "limit": min(max(int(limit), 1), 50),
                            "active_only": True,
                            "video_only": True,
                        },
                        timeout=httpx.Timeout(self.timeout_seconds),
                    ) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.strip():
                                continue
                            try:
                                event = json.loads(line)
                            except json.JSONDecodeError as exc:
                                raise CollectorUnavailable(
                                    "collector returned malformed event"
                                ) from exc
                            if event.get("type") == "error":
                                raise CollectorUnavailable(
                                    str(event.get("message") or "collector failed")
                                )
                            if event.get("type") != "ad" or not isinstance(event.get("ad"), dict):
                                continue
                            normalized = normalize_collector_ad(event["ad"])
                            if normalized["ad_library_id"]:
                                ads.append(CollectorAd.model_validate(normalized))
                    return ads
                except httpx.RemoteProtocolError as exc:
                    if attempt + 1 >= self._REMOTE_PROTOCOL_ATTEMPTS:
                        raise CollectorUnavailable(
                            f"collector bridge unavailable: {exc.__class__.__name__}"
                        ) from exc
                    await asyncio.sleep(0.25 * (attempt + 1))
                except httpx.HTTPError as exc:
                    raise CollectorUnavailable(
                        f"collector bridge unavailable: {exc.__class__.__name__}"
                    ) from exc
        finally:
            if owns_client:
                await client.aclose()
        raise AssertionError("unreachable")


async def iter_collector_ads(
    adapter: AdSourceAdapter, **kwargs: object
) -> AsyncIterator[CollectorAd]:
    for ad in await adapter.collect(**kwargs):
        yield ad


def _first(items: list | tuple | None) -> object | None:
    return items[0] if items else None


def _optional_text(value: object | None) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int_or_none(value: object | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
