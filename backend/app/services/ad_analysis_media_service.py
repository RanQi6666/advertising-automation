from __future__ import annotations

import asyncio
import shutil
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backend.app.core.config import get_settings
from backend.app.services.safe_public_http import (
    SafePublicHTTPError,
    download_public_https_file,
    extension_from_url_or_content_type,
)

_IMAGE_MAGIC = {
    b"\xff\xd8\xff": "jpeg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"RIFF": "webp_or_riff",
    b"GIF87a": "gif",
    b"GIF89a": "gif",
}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_SUPPORTED_VIDEO_PROBE_FORMATS = {"mp4", "mov", "webm", "matroska"}


@dataclass(frozen=True)
class MediaProcessingResult:
    summary: dict[str, Any]
    working_dir: Path | None = None


class AdAnalysisMediaService:
    """Downloads and derives private media artifacts for external ad analysis jobs."""

    async def process_media(
        self, payload: dict[str, Any], *, analysis_id: str
    ) -> MediaProcessingResult:
        settings = get_settings()
        creative = payload.get("creative") if isinstance(payload.get("creative"), dict) else {}
        creative_type = str(creative.get("creative_type") or "").strip().lower()
        source_url = str(
            creative.get("image_url")
            if creative_type == "image"
            else creative.get("video_url") or ""
        ).strip()
        base_summary: dict[str, Any] = {
            "status": "skipped",
            "creative_type": creative_type or "unknown",
            "source_url": source_url or None,
            "thumbnail_generated": False,
            "keyframes_generated": False,
            "local_artifacts": {},
            "warnings": [],
        }
        if not getattr(settings, "ad_analysis_media_processing_enabled", True):
            base_summary["warnings"].append("Media processing is disabled by configuration.")
            return MediaProcessingResult(base_summary)
        if creative_type not in {"image", "video"} or not source_url:
            base_summary["status"] = "unavailable"
            base_summary["warnings"].append("No supported creative media URL was supplied.")
            return MediaProcessingResult(base_summary)

        root = Path(settings.ad_analysis_media_root)
        working_dir = root / _safe_path_segment(analysis_id)
        working_dir.mkdir(parents=True, exist_ok=True)
        try:
            return await self._process_with_download(
                payload=payload,
                creative_type=creative_type,
                source_url=source_url,
                working_dir=working_dir,
                base_summary=base_summary,
            )
        except Exception as exc:  # noqa: BLE001 - media failures degrade analysis, never fail job.
            base_summary["status"] = "unavailable"
            base_summary["warnings"].append(_warning_from_exception(exc))
            return MediaProcessingResult(base_summary, working_dir)

    async def _process_with_download(
        self,
        *,
        payload: dict[str, Any],
        creative_type: str,
        source_url: str,
        working_dir: Path,
        base_summary: dict[str, Any],
    ) -> MediaProcessingResult:
        settings = get_settings()
        fallback = ".jpg" if creative_type == "image" else ".mp4"
        suffix = extension_from_url_or_content_type(source_url, None, fallback)
        if creative_type == "video" and suffix.lower() not in _VIDEO_SUFFIXES:
            suffix = ".mp4"
        if creative_type == "image" and suffix.lower() not in _IMAGE_SUFFIXES:
            suffix = ".jpg"
        downloaded_path = working_dir / f"source{suffix}"
        downloaded = await download_public_https_file(
            source_url,
            downloaded_path,
            allowed_hosts=settings.ad_analysis_allowed_media_hosts,
            max_bytes=(
                settings.ad_analysis_video_download_max_bytes
                if creative_type == "video"
                else settings.ad_analysis_image_download_max_bytes
            ),
            timeout_seconds=settings.ad_analysis_media_download_timeout_seconds,
            allow_private_networks=settings.ad_analysis_allow_private_media_hosts,
        )
        base_summary["final_url"] = downloaded.final_url
        base_summary["download"] = {
            "bytes": downloaded.bytes_written,
            "content_type": downloaded.content_type,
        }
        base_summary["local_artifacts"]["downloaded_path"] = str(downloaded.path)

        if creative_type == "image":
            await self._validate_image(downloaded.path)
            thumbnail_path = working_dir / "thumbnail.jpg"
            thumbnail_generated = await _generate_image_thumbnail(downloaded.path, thumbnail_path)
            base_summary.update(
                {
                    "status": "available",
                    "thumbnail_generated": thumbnail_generated,
                    "keyframes_generated": False,
                }
            )
            if thumbnail_generated:
                base_summary["local_artifacts"]["thumbnail_path"] = str(thumbnail_path)
            else:
                base_summary["warnings"].append(
                    "Pillow is unavailable; image thumbnail was not generated."
                )
            return MediaProcessingResult(base_summary, working_dir)

        await self._validate_video(downloaded.path)
        video_meta = await _probe_video(downloaded.path)
        _validate_probed_video_format(video_meta)
        duration = video_meta.get("duration_seconds")
        if (
            isinstance(duration, int | float)
            and duration > settings.ad_analysis_video_max_duration_seconds
        ):
            raise SafePublicHTTPError(
                f"Video duration {duration:.2f}s exceeds configured "
                f"{settings.ad_analysis_video_max_duration_seconds}s limit."
            )
        thumbnail_path = working_dir / "thumbnail.jpg"
        keyframe_paths = await _extract_video_keyframes(downloaded.path, working_dir, duration)
        thumbnail_generated = thumbnail_path.exists()
        base_summary.update(
            {
                "status": "available" if keyframe_paths else "partial",
                "thumbnail_generated": thumbnail_generated,
                "keyframes_generated": bool(keyframe_paths),
                "video": video_meta,
            }
        )
        if thumbnail_generated:
            base_summary["local_artifacts"]["thumbnail_path"] = str(thumbnail_path)
        base_summary["local_artifacts"]["keyframe_paths"] = [str(path) for path in keyframe_paths]
        if not keyframe_paths:
            base_summary["warnings"].append(
                "ffmpeg is unavailable or failed; video keyframes were not generated."
            )
        return MediaProcessingResult(base_summary, working_dir)

    async def _validate_image(self, path: Path) -> None:
        header = path.read_bytes()[:16]
        if not any(header.startswith(magic) for magic in _IMAGE_MAGIC):
            raise SafePublicHTTPError("downloaded file is not a recognized image")

    async def _validate_video(self, path: Path) -> None:
        if path.suffix.lower() not in _VIDEO_SUFFIXES:
            raise SafePublicHTTPError("video must be MP4, MOV, or WebM")

    async def cleanup_analysis_media(self, analysis_id: str) -> None:
        path = Path(get_settings().ad_analysis_media_root) / _safe_path_segment(analysis_id)
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    async def cleanup_orphans(self, *, older_than_hours: int = 2) -> int:
        root = Path(get_settings().ad_analysis_media_root)
        if not root.exists():
            return 0
        cutoff = datetime.now(UTC) - timedelta(hours=max(older_than_hours, 1))
        removed = 0
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                modified = datetime.fromtimestamp(child.stat().st_mtime, UTC)
            except OSError:
                continue
            if modified < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        return removed


async def _generate_image_thumbnail(source: Path, destination: Path) -> bool:
    try:
        from PIL import Image
    except Exception:  # pragma: no cover - optional dependency missing
        return False

    def _work() -> bool:
        with Image.open(source) as image:
            image.thumbnail((512, 512))
            rgb = image.convert("RGB")
            rgb.save(destination, format="JPEG", quality=85, optimize=True)
        return True

    return await asyncio.to_thread(_work)


async def _probe_video(source: Path) -> dict[str, Any]:
    if shutil.which("ffprobe") is None:
        raise SafePublicHTTPError("ffprobe is unavailable; video media cannot be verified")
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name:format=duration,format_name",
        "-of",
        "default=noprint_wrappers=1:nokey=0",
        str(source),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    timeout_seconds = get_settings().ad_analysis_ffprobe_timeout_seconds
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise SafePublicHTTPError(
            f"ffprobe timed out after {timeout_seconds:g} seconds"
        ) from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="ignore").strip()[:200]
        suffix = f": {detail}" if detail else ""
        raise SafePublicHTTPError(f"ffprobe could not verify downloaded video{suffix}")
    values: dict[str, str] = {}
    for line in stdout.decode("utf-8", errors="ignore").splitlines():
        key, _, value = line.partition("=")
        if key and value:
            values[key] = value
    duration: float | None = None
    try:
        duration = float(values.get("duration") or "")
    except ValueError:
        duration = None
    if duration is None or duration <= 0 or not values.get("format_name"):
        raise SafePublicHTTPError("ffprobe returned incomplete or invalid video metadata")
    return {
        "duration_seconds": duration,
        "format": values.get("format_name") or source.suffix.lower().lstrip("."),
        "codec": values.get("codec_name"),
    }


async def _extract_video_keyframes(source: Path, working_dir: Path, duration: Any) -> list[Path]:
    if shutil.which("ffmpeg") is None:
        raise SafePublicHTTPError("ffmpeg is unavailable; video keyframes cannot be generated")
    seconds = _keyframe_seconds(duration)
    results: list[Path] = []
    timeout_seconds = get_settings().ad_analysis_ffmpeg_frame_timeout_seconds
    for index, second in enumerate(seconds, start=1):
        destination = working_dir / ("thumbnail.jpg" if index == 1 else f"keyframe_{index}.jpg")
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
        try:
            await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            destination.unlink(missing_ok=True)
            raise SafePublicHTTPError(
                f"ffmpeg frame extraction timed out after {timeout_seconds:g} seconds"
            ) from exc
        if process.returncode == 0 and destination.exists() and destination.stat().st_size > 0:
            results.append(destination)
    return results


def _keyframe_seconds(duration: Any) -> list[float]:
    try:
        value = float(duration)
    except (TypeError, ValueError):
        value = 12.0
    value = max(value, 1.0)
    candidates = [0.1, min(value * 0.33, value - 0.1), min(value * 0.66, value - 0.1)]
    result: list[float] = []
    for second in candidates:
        clean = max(float(second), 0.0)
        if clean not in result:
            result.append(clean)
    return result or [0.0]


def _validate_probed_video_format(video_meta: dict[str, Any]) -> None:
    format_value = str(video_meta.get("format") or "").lower()
    tokens = {token.strip() for token in format_value.split(",") if token.strip()}
    if not tokens or not tokens.intersection(_SUPPORTED_VIDEO_PROBE_FORMATS):
        raise SafePublicHTTPError("downloaded video must be MP4, MOV, or WebM")


def _safe_path_segment(value: str) -> str:
    return (
        "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)[:96] or "unknown"
    )


def _warning_from_exception(exc: Exception) -> str:
    parts: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        label = current.__class__.__name__
        detail = f"{label}: {text}" if text and text != label else label
        if detail not in parts:
            parts.append(detail)
        current = current.__cause__ or current.__context__
    message = "; caused by ".join(parts)[:500] or exc.__class__.__name__
    return f"Media processing unavailable: {message}"


def media_source_domain(summary: dict[str, Any]) -> str | None:
    source_url = summary.get("source_url")
    if not isinstance(source_url, str):
        return None
    parsed = urlparse(source_url)
    return parsed.hostname


def public_media_summary(summary: dict[str, Any] | None) -> dict[str, Any]:
    """Return the stable external media summary without private worker paths."""

    public_summary = deepcopy(summary) if isinstance(summary, dict) else {}
    public_summary.pop("local_artifacts", None)
    return public_summary
