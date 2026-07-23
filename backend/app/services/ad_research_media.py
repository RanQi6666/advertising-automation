from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from backend.app.core.config import get_settings
from backend.app.schemas.ad_research import CollectorAd
from backend.app.services.safe_public_http import (
    SafePublicHTTPError,
    download_public_http_file,
    validate_public_http_url,
)

MAX_VIDEO_SECONDS = 30.0
MIN_ACTIVE_DAYS = 1


@dataclass(frozen=True)
class PreparedAdMedia:
    cover_url: str | None
    cover_source: Literal["original_thumbnail", "generated_frame"] | None
    frame_urls: tuple[str, ...]
    local_frame_paths: tuple[Path, ...]
    duration_source: Literal["collector", "remote_ffprobe", "downloaded_ffprobe"] | None
    duration_probe_attempts: int


@dataclass(frozen=True)
class TechnicalQualification:
    qualified: bool
    reasons: tuple[str, ...]
    duration_seconds: float | None
    active_days: int | None
    media: PreparedAdMedia | None = None


class AdResearchMediaInspector:
    """Prepare public video evidence without opening or following advertising landing pages."""

    def __init__(self) -> None:
        self.settings = get_settings()
        self._frame_semaphore = asyncio.Semaphore(
            max(int(self.settings.ad_research_frame_concurrency), 1)
        )

    async def inspect(self, ad: CollectorAd, *, job_id: str) -> TechnicalQualification:
        reasons = await self._base_reasons(ad)
        if reasons:
            return TechnicalQualification(False, tuple(reasons), ad.duration_seconds, ad.days_running)

        artifact_dir = self._artifact_dir(job_id, ad.ad_library_id)
        duration, duration_source, probe_attempts, downloaded_video = await self._resolve_duration(
            ad, artifact_dir
        )
        if duration is None:
            self._delete_artifact_dir(artifact_dir)
            return TechnicalQualification(False, ("duration_unavailable",), None, ad.days_running)
        if duration > MAX_VIDEO_SECONDS:
            self._delete_artifact_dir(artifact_dir)
            return TechnicalQualification(
                False, ("duration_over_30",), duration, ad.days_running
            )

        if downloaded_video is None:
            downloaded_video = await self._download_video(ad.video_url or "", artifact_dir / "video.mp4")
        if downloaded_video is None:
            self._delete_artifact_dir(artifact_dir)
            return TechnicalQualification(
                False, ("video_download_failed",), duration, ad.days_running
            )

        prepared = await self._prepare_cover_and_frames(
            ad=ad,
            downloaded_video=downloaded_video,
            artifact_dir=artifact_dir,
            duration=duration,
            duration_source=duration_source,
            duration_probe_attempts=probe_attempts,
        )
        if prepared is None:
            self._delete_artifact_dir(artifact_dir)
            return TechnicalQualification(
                False, ("no_analyzable_visual",), duration, ad.days_running
            )
        return TechnicalQualification(True, (), duration, ad.days_running, prepared)

    async def inspect_many(
        self, ads: list[CollectorAd], *, job_id: str
    ) -> dict[str, TechnicalQualification]:
        semaphore = asyncio.Semaphore(max(int(self.settings.ad_research_media_concurrency), 1))
        ordered = sorted(ads, key=_media_priority_key)

        async def inspect_one(ad: CollectorAd) -> tuple[str, TechnicalQualification]:
            async with semaphore:
                return ad.ad_library_id, await self.inspect(ad, job_id=job_id)

        pairs = await asyncio.gather(*(inspect_one(ad) for ad in ordered))
        return dict(pairs)

    async def is_technically_qualified(self, ad: CollectorAd, *, job_id: str = "validation") -> bool:
        return (await self.inspect(ad, job_id=job_id)).qualified

    async def _base_reasons(self, ad: CollectorAd) -> list[str]:
        reasons: list[str] = []
        if (ad.status or "").upper() != "ACTIVE":
            reasons.append("status_not_active")
        if not ad.video_url:
            reasons.append("missing_video")
        else:
            try:
                await validate_public_http_url(ad.video_url)
            except SafePublicHTTPError:
                reasons.append("video_url_rejected")
        if ad.days_running is None or ad.days_running < MIN_ACTIVE_DAYS:
            reasons.append("active_days_below_minimum")
        return reasons

    async def _resolve_duration(
        self, ad: CollectorAd, artifact_dir: Path
    ) -> tuple[
        float | None,
        Literal["collector", "remote_ffprobe", "downloaded_ffprobe"] | None,
        int,
        Path | None,
    ]:
        if ad.duration_seconds is not None and ad.duration_seconds >= 0:
            return float(ad.duration_seconds), "collector", 0, None

        remote_attempts = max(int(self.settings.ad_research_media_retry_attempts), 0) + 1
        for attempt in range(1, remote_attempts + 1):
            duration = await self.probe_remote_duration(ad.video_url or "", verify_public=False)
            if duration is not None:
                ad.duration_seconds = duration
                return duration, "remote_ffprobe", attempt, None
            if attempt < remote_attempts:
                await asyncio.sleep(0.25 * (2 ** (attempt - 1)))

        downloaded_video = await self._download_video(ad.video_url or "", artifact_dir / "video.mp4")
        if downloaded_video is None:
            return None, None, remote_attempts, None
        duration = await self._probe_local_video(downloaded_video)
        if duration is None:
            return None, None, remote_attempts, downloaded_video
        ad.duration_seconds = duration
        return duration, "downloaded_ffprobe", remote_attempts, downloaded_video

    async def _download_video(self, video_url: str, destination: Path) -> Path | None:
        try:
            downloaded = await download_public_http_file(
                video_url,
                destination,
                max_bytes=self.settings.ad_research_media_download_max_bytes,
                timeout_seconds=self.settings.ad_research_media_download_timeout_seconds,
                retry_attempts=self.settings.ad_research_media_retry_attempts,
            )
            return downloaded.path
        except (SafePublicHTTPError, OSError):
            destination.unlink(missing_ok=True)
            return None

    async def _prepare_cover_and_frames(
        self,
        *,
        ad: CollectorAd,
        downloaded_video: Path,
        artifact_dir: Path,
        duration: float,
        duration_source: Literal["collector", "remote_ffprobe", "downloaded_ffprobe"] | None,
        duration_probe_attempts: int,
    ) -> PreparedAdMedia | None:
        frame_paths = await self._extract_frames(downloaded_video, artifact_dir, duration)
        original_cover = await self._download_original_cover(ad.thumbnail_url, artifact_dir / "cover.jpg")
        cover_source: Literal["original_thumbnail", "generated_frame"] | None = None
        cover_path: Path | None = None
        if original_cover is not None:
            cover_path = original_cover
            cover_source = "original_thumbnail"
        elif frame_paths:
            cover_path = artifact_dir / "cover.jpg"
            await asyncio.to_thread(shutil.copyfile, frame_paths[0], cover_path)
            cover_source = "generated_frame"

        if cover_path is None:
            return None

        local_visual_paths = tuple(
            dict.fromkeys([cover_path, *frame_paths])
        )
        return PreparedAdMedia(
            cover_url=self._public_url(cover_path),
            cover_source=cover_source,
            frame_urls=tuple(self._public_url(path) for path in frame_paths),
            local_frame_paths=local_visual_paths,
            duration_source=duration_source,
            duration_probe_attempts=duration_probe_attempts,
        )

    async def _download_original_cover(self, thumbnail_url: str | None, destination: Path) -> Path | None:
        if not thumbnail_url:
            return None
        try:
            await validate_public_http_url(thumbnail_url)
            downloaded = await download_public_http_file(
                thumbnail_url,
                destination,
                max_bytes=min(self.settings.ad_research_media_download_max_bytes, 10 * 1024 * 1024),
                timeout_seconds=self.settings.ad_research_media_download_timeout_seconds,
                retry_attempts=self.settings.ad_research_media_retry_attempts,
            )
        except (SafePublicHTTPError, OSError):
            destination.unlink(missing_ok=True)
            return None
        if not await asyncio.to_thread(_is_decodable_image, downloaded.path):
            downloaded.path.unlink(missing_ok=True)
            return None
        if downloaded.path != destination:
            await asyncio.to_thread(shutil.move, str(downloaded.path), str(destination))
        return destination

    async def _extract_frames(
        self, source: Path, artifact_dir: Path, duration: float
    ) -> tuple[Path, ...]:
        seconds = _frame_seconds(duration)

        async def extract(second: float, ratio: int) -> Path | None:
            async with self._frame_semaphore:
                return await self._extract_frame(source, artifact_dir / f"frame_{ratio}.jpg", second)

        paths = await asyncio.gather(
            *(extract(second, ratio) for second, ratio in zip(seconds, (20, 50, 80), strict=True))
        )
        return tuple(path for path in paths if path is not None)

    async def _extract_frame(self, source: Path, destination: Path, second: float) -> Path | None:
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-y",
                "-ss",
                f"{second:.2f}",
                "-i",
                str(source),
                "-frames:v",
                "1",
                "-q:v",
                "3",
                str(destination),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(
                process.communicate(), timeout=self.settings.ad_research_ffmpeg_frame_timeout_seconds
            )
            if process.returncode == 0 and destination.exists() and destination.stat().st_size > 0:
                return destination
        except TimeoutError:
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
        except OSError:
            pass
        destination.unlink(missing_ok=True)
        return None

    async def probe_remote_duration(
        self, video_url: str, *, verify_public: bool = True
    ) -> float | None:
        if verify_public:
            try:
                await validate_public_http_url(video_url)
            except SafePublicHTTPError:
                return None
        return await self._probe_duration_source(video_url)

    async def probe_duration(self, video_url: str, *, verify_public: bool = True) -> float | None:
        """Backward-compatible alias for callers that only need remote public metadata."""
        return await self.probe_remote_duration(video_url, verify_public=verify_public)

    async def _probe_local_video(self, source: Path) -> float | None:
        return await self._probe_duration_source(str(source))

    async def _probe_duration_source(self, source: str) -> float | None:
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
                source,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=self.settings.ad_research_ffprobe_timeout_seconds
            )
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

    def _artifact_dir(self, job_id: str, ad_library_id: str) -> Path:
        root = self._media_root()
        job_segment = _safe_path_segment(job_id)
        ad_segment = _safe_path_segment(ad_library_id)
        artifact_dir = (root / job_segment / ad_segment).resolve()
        _ensure_within(artifact_dir, root)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        return artifact_dir

    async def retain_only(self, job_id: str, ad_library_ids: set[str]) -> None:
        job_dir = self._job_dir(job_id)
        if not job_dir.exists():
            return
        allowed = {_safe_path_segment(ad_library_id) for ad_library_id in ad_library_ids}
        for child in job_dir.iterdir():
            if child.name not in allowed:
                self._delete_artifact_dir(child)

    async def cleanup_job_media(self, job_id: str) -> None:
        job_dir = self._job_dir(job_id)
        self._delete_artifact_dir(job_dir)

    def _job_dir(self, job_id: str) -> Path:
        root = self._media_root()
        job_dir = (root / _safe_path_segment(job_id)).resolve()
        _ensure_within(job_dir, root)
        return job_dir

    def _media_root(self) -> Path:
        configured = Path(self.settings.ad_research_media_root)
        storage_root = Path(self.settings.local_storage_root).resolve()
        root = configured if configured.is_absolute() else storage_root / configured
        root = root.resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _delete_artifact_dir(self, target: Path) -> None:
        root = self._media_root()
        resolved_target = target.resolve()
        _ensure_within(resolved_target, root)
        if resolved_target == root:
            raise ValueError("refusing to delete the ad research media root")
        shutil.rmtree(resolved_target, ignore_errors=True)

    def _public_url(self, path: Path) -> str:
        storage_root = Path(self.settings.local_storage_root).resolve()
        resolved_path = path.resolve()
        _ensure_within(resolved_path, storage_root)
        relative = resolved_path.relative_to(storage_root).as_posix()
        return f"{self.settings.public_base_url.rstrip('/')}/storage/{quote(relative, safe='/')}"


def _frame_seconds(duration: float, *, low_confidence: bool = False) -> tuple[float, ...]:
    ratios = (0.20, 0.50, 0.80, 0.35, 0.65) if low_confidence else (0.20, 0.50, 0.80)
    return tuple(
        min(max(duration * ratio, 0.0), max(duration - 0.05, 0.0)) for ratio in ratios
    )


def _media_priority_key(ad: CollectorAd) -> tuple[bool, float, str]:
    duration = ad.duration_seconds
    return (duration is None, float(duration or 0), ad.ad_library_id)


def _safe_path_segment(value: str) -> str:
    segment = str(value).strip()
    if not segment or segment in {".", ".."} or "/" in segment or "\\" in segment:
        raise ValueError("ad research media path contains an unsafe segment")
    return segment


def _ensure_within(path: Path, root: Path) -> None:
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("ad research media path escapes the configured root") from exc


def _is_decodable_image(path: Path) -> bool:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        return True
    except Exception:
        return False
