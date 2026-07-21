from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from backend.app.schemas.ad_research import CollectorAd

MAX_VIDEO_SECONDS = 30.0
MIN_ACTIVE_DAYS = 3


class AdResearchMediaInspector:
    """Conservative public-media checks; it never opens or follows landing pages."""

    async def is_technically_qualified(self, ad: CollectorAd) -> bool:
        if (ad.status or "").upper() != "ACTIVE":
            return False
        if not ad.video_url or not ad.thumbnail_url:
            return False
        if not _is_public_http_url(ad.video_url) or not _is_public_http_url(ad.thumbnail_url):
            return False
        if (ad.days_running or 0) < MIN_ACTIVE_DAYS:
            return False
        duration = ad.duration_seconds
        if duration is None:
            duration = await self.probe_duration(ad.video_url)
            ad.duration_seconds = duration
        return duration is not None and duration <= MAX_VIDEO_SECONDS

    async def probe_duration(self, video_url: str) -> float | None:
        """Ask ffprobe for public media metadata without downloading a landing page."""
        try:
            process = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=12)
            if process.returncode != 0:
                return None
            duration = float(stdout.decode("utf-8", errors="ignore").strip())
            return duration if duration >= 0 else None
        except (OSError, ValueError, TimeoutError):
            return None


def _is_public_http_url(value: str) -> bool:
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        return False
    return host not in {"localhost", "127.0.0.1", "::1"} and not host.endswith(".local")
