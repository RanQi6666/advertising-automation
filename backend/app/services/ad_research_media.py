from __future__ import annotations

import asyncio
from dataclasses import dataclass

from backend.app.schemas.ad_research import CollectorAd
from backend.app.services.safe_public_http import SafePublicHTTPError, validate_public_http_url

MAX_VIDEO_SECONDS = 30.0
MIN_ACTIVE_DAYS = 1


@dataclass(frozen=True)
class TechnicalQualification:
    qualified: bool
    reasons: tuple[str, ...]
    duration_seconds: float | None
    active_days: int | None


class AdResearchMediaInspector:
    """Conservative public-media checks; it never opens or follows landing pages."""

    async def inspect(self, ad: CollectorAd) -> TechnicalQualification:
        reasons: list[str] = []
        if (ad.status or "").upper() != "ACTIVE":
            reasons.append("status_not_active")

        video_is_public = False
        if not ad.video_url:
            reasons.append("missing_video")
        else:
            try:
                await validate_public_http_url(ad.video_url)
                video_is_public = True
            except SafePublicHTTPError:
                reasons.append("video_url_rejected")

        if not ad.thumbnail_url:
            reasons.append("missing_thumbnail")
        else:
            try:
                await validate_public_http_url(ad.thumbnail_url)
            except SafePublicHTTPError:
                reasons.append("thumbnail_url_rejected")

        if ad.days_running is None or ad.days_running < MIN_ACTIVE_DAYS:
            reasons.append("active_days_below_minimum")

        duration = ad.duration_seconds
        if duration is None and video_is_public and ad.video_url:
            duration = await self.probe_duration(ad.video_url, verify_public=False)
            ad.duration_seconds = duration
        if duration is None:
            reasons.append("duration_unavailable")
        elif duration > MAX_VIDEO_SECONDS:
            reasons.append("duration_over_30")

        return TechnicalQualification(
            qualified=not reasons,
            reasons=tuple(reasons),
            duration_seconds=duration,
            active_days=ad.days_running,
        )

    async def is_technically_qualified(self, ad: CollectorAd) -> bool:
        return (await self.inspect(ad)).qualified

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
