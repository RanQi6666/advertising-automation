from __future__ import annotations

import asyncio

from backend.app.schemas.ad_research import CollectorAd
from backend.app.services.safe_public_http import SafePublicHTTPError, validate_public_http_url

MAX_VIDEO_SECONDS = 30.0
MIN_ACTIVE_DAYS = 3


class AdResearchMediaInspector:
    """Conservative public-media checks; it never opens or follows landing pages."""

    async def is_technically_qualified(self, ad: CollectorAd) -> bool:
        if (ad.status or "").upper() != "ACTIVE":
            return False
        if not ad.video_url or not ad.thumbnail_url:
            return False
        try:
            await validate_public_http_url(ad.video_url)
            await validate_public_http_url(ad.thumbnail_url)
        except SafePublicHTTPError:
            return False
        if (ad.days_running or 0) < MIN_ACTIVE_DAYS:
            return False
        duration = ad.duration_seconds
        if duration is None:
            duration = await self.probe_duration(ad.video_url, verify_public=False)
            ad.duration_seconds = duration
        return duration is not None and duration <= MAX_VIDEO_SECONDS

    async def probe_duration(self, video_url: str, *, verify_public: bool = True) -> float | None:
        """Ask ffprobe for public media metadata without downloading a landing page."""
        if verify_public:
            try:
                await validate_public_http_url(video_url)
            except SafePublicHTTPError:
                return None
        process: asyncio.subprocess.Process | None = None
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
        except TimeoutError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
            return None
        except (OSError, ValueError):
            return None
