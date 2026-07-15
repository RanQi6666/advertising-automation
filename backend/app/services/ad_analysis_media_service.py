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
    download_public_http_file,
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


@dataclass(frozen=True)
class VideoFrameExtractionResult:
    first_frame_path: Path | None
    middle_frame_paths: list[Path]
    last_frame_path: Path | None
    warnings: list[str]

    @property
    def ordered_paths(self) -> list[Path]:
        paths: list[Path] = []
        if self.first_frame_path is not None:
            paths.append(self.first_frame_path)
        paths.extend(self.middle_frame_paths)
        if self.last_frame_path is not None:
            paths.append(self.last_frame_path)
        return paths


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
            else creative.get("video_url") if creative_type == "video" else ""
        ).strip()
        carousel_urls = creative.get("image_urls") if creative_type == "carousel" else []
        if not isinstance(carousel_urls, list):
            carousel_urls = []
        base_summary: dict[str, Any] = {
            "status": "skipped",
            "creative_type": creative_type or "unknown",
            "source_url": source_url or None,
            "source_urls": carousel_urls if creative_type == "carousel" else None,
            "thumbnail_generated": False,
            "keyframes_generated": False,
            "local_artifacts": {},
            "warnings": [],
        }
        if not getattr(settings, "ad_analysis_media_processing_enabled", True):
            base_summary["warnings"].append("Media processing is disabled by configuration.")
            return MediaProcessingResult(base_summary)
        if creative_type == "carousel" and carousel_urls:
            source_url = str(carousel_urls[0]).strip()
            base_summary["source_url"] = source_url or None
        if creative_type not in {"image", "video", "carousel"} or not source_url:
            base_summary["status"] = "unavailable"
            base_summary["warnings"].append("No supported creative media URL was supplied.")
            return MediaProcessingResult(base_summary)

        root = Path(settings.ad_analysis_media_root)
        working_dir = root / _safe_path_segment(analysis_id)
        working_dir.mkdir(parents=True, exist_ok=True)
        try:
            if creative_type == "carousel":
                return await self._process_carousel(
                    image_urls=[str(url).strip() for url in carousel_urls],
                    working_dir=working_dir,
                    base_summary=base_summary,
                )
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

    async def _process_carousel(
        self,
        *,
        image_urls: list[str],
        working_dir: Path,
        base_summary: dict[str, Any],
    ) -> MediaProcessingResult:
        settings = get_settings()
        thumbnail_paths: list[str] = []
        cards: list[dict[str, Any]] = []
        for index, source_url in enumerate(image_urls, start=1):
            card: dict[str, Any] = {"index": index, "source_url": source_url}
            try:
                suffix = extension_from_url_or_content_type(source_url, None, ".jpg")
                if suffix.lower() not in _IMAGE_SUFFIXES:
                    suffix = ".jpg"
                downloaded_path = working_dir / f"card_{index}_source{suffix}"
                downloaded = await download_public_http_file(
                    source_url,
                    downloaded_path,
                    max_bytes=settings.ad_analysis_image_download_max_bytes,
                    timeout_seconds=settings.ad_analysis_image_download_timeout_seconds,
                    allow_private_networks=settings.ad_analysis_allow_private_media_hosts,
                    retry_attempts=settings.ad_analysis_media_download_retry_attempts,
                    retry_delay_seconds=settings.ad_analysis_media_download_retry_delay_seconds,
                )
                await self._validate_image(downloaded.path)
                thumbnail_path = working_dir / f"card_{index}_thumbnail.jpg"
                if not await _generate_image_thumbnail(downloaded.path, thumbnail_path):
                    raise SafePublicHTTPError(
                        "Pillow is unavailable; image thumbnail was not generated"
                    )
                thumbnail_paths.append(str(thumbnail_path))
                card.update(
                    {
                        "status": "available",
                        "final_url": downloaded.final_url,
                        "bytes": downloaded.bytes_written,
                        "download_attempts": downloaded.attempts,
                    }
                )
            except Exception as exc:  # noqa: BLE001 - remaining cards may still be useful.
                card["status"] = "unavailable"
                card["warning"] = _warning_from_exception(exc)
                base_summary["warnings"].append(
                    f"Carousel card {index} was unavailable: {_warning_from_exception(exc)}"
                )
            cards.append(card)

        base_summary["cards"] = cards
        base_summary["local_artifacts"]["carousel_thumbnail_paths"] = thumbnail_paths
        base_summary["thumbnail_generated"] = bool(thumbnail_paths)
        base_summary["keyframes_generated"] = False
        if not thumbnail_paths:
            base_summary["status"] = "unavailable"
        elif len(thumbnail_paths) == len(image_urls):
            base_summary["status"] = "available"
        else:
            base_summary["status"] = "partial"
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
        timeout_seconds = (
            settings.ad_analysis_video_download_timeout_seconds
            if creative_type == "video"
            else settings.ad_analysis_image_download_timeout_seconds
        )
        downloaded = await download_public_http_file(
            source_url,
            downloaded_path,
            max_bytes=(
                settings.ad_analysis_video_download_max_bytes
                if creative_type == "video"
                else settings.ad_analysis_image_download_max_bytes
            ),
            timeout_seconds=timeout_seconds,
            allow_private_networks=settings.ad_analysis_allow_private_media_hosts,
            retry_attempts=settings.ad_analysis_media_download_retry_attempts,
            retry_delay_seconds=settings.ad_analysis_media_download_retry_delay_seconds,
        )
        base_summary["final_url"] = downloaded.final_url
        base_summary["download"] = {
            "bytes": downloaded.bytes_written,
            "content_type": downloaded.content_type,
            "attempts": downloaded.attempts,
            "retry_used": downloaded.attempts > 1,
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
        frames = await _extract_video_keyframes(downloaded.path, working_dir, duration)
        keyframe_paths = frames.ordered_paths
        thumbnail_generated = frames.first_frame_path is not None
        visual_complete = (
            frames.first_frame_path is not None and frames.last_frame_path is not None
        )
        base_summary.update(
            {
                "status": "available" if visual_complete else "partial",
                "thumbnail_generated": thumbnail_generated,
                "keyframes_generated": bool(keyframe_paths),
                "video": video_meta,
                "first_frame_generated": frames.first_frame_path is not None,
                "last_frame_generated": frames.last_frame_path is not None,
                "middle_frame_count": len(frames.middle_frame_paths),
                "frame_count": len(keyframe_paths),
                "video_visual_complete": visual_complete,
            }
        )
        if frames.first_frame_path is not None:
            base_summary["local_artifacts"]["thumbnail_path"] = str(frames.first_frame_path)
            base_summary["local_artifacts"]["first_frame_path"] = str(frames.first_frame_path)
        if frames.last_frame_path is not None:
            base_summary["local_artifacts"]["last_frame_path"] = str(frames.last_frame_path)
        base_summary["local_artifacts"]["keyframe_paths"] = [str(path) for path in keyframe_paths]
        base_summary["warnings"].extend(frames.warnings)
        if not visual_complete:
            base_summary["warnings"].insert(
                0,
                "Video visual processing is incomplete because the opening or ending "
                "frame could not be extracted.",
            )
        elif not keyframe_paths:
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


async def _extract_video_keyframes(
    source: Path, working_dir: Path, duration: Any
) -> VideoFrameExtractionResult:
    """Derive mandatory opening/ending frames and up to three distinct middle frames."""

    if shutil.which("ffmpeg") is None:
        return VideoFrameExtractionResult(
            None, [], None, ["ffmpeg is unavailable; video frames were not generated."]
        )

    first_second, last_second, middle_seconds = _video_frame_seconds(duration)
    warnings: list[str] = []
    first = await _extract_video_frame(
        source, working_dir / "first_frame.jpg", first_second, warnings
    )
    last = await _extract_video_frame(
        source, working_dir / "last_frame.jpg", last_second, warnings
    )
    candidates: list[Path] = []
    for index, second in enumerate(middle_seconds, start=1):
        path = await _extract_video_frame(
            source, working_dir / f"middle_candidate_{index}.jpg", second, warnings
        )
        if path is not None:
            candidates.append(path)

    middle = await _select_distinct_middle_frames(candidates, first, last)
    selected = set(middle)
    for index, path in enumerate(middle, start=1):
        final_path = working_dir / f"middle_frame_{index}.jpg"
        path.replace(final_path)
        selected.remove(path)
        selected.add(final_path)
        middle[index - 1] = final_path
    for path in candidates:
        if path not in selected:
            path.unlink(missing_ok=True)
    return VideoFrameExtractionResult(first, middle, last, warnings)


async def _extract_video_frame(
    source: Path, destination: Path, second: float, warnings: list[str]
) -> Path | None:
    timeout_seconds = get_settings().ad_analysis_ffmpeg_frame_timeout_seconds
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
        try:
            await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.wait()
            warnings.append(
                f"ffmpeg frame extraction timed out after {timeout_seconds:g} seconds."
            )
            destination.unlink(missing_ok=True)
            return None
        if process.returncode == 0 and destination.exists() and destination.stat().st_size > 0:
            return destination
        destination.unlink(missing_ok=True)
        warnings.append(f"ffmpeg could not extract video frame near {second:.2f}s.")
    except OSError as exc:  # pragma: no cover - depends on host process failure
        destination.unlink(missing_ok=True)
        warnings.append(f"ffmpeg could not start: {exc}")
    return None


async def _select_distinct_middle_frames(
    candidates: list[Path], first: Path | None, last: Path | None
) -> list[Path]:
    selected: list[Path] = []
    references = [path for path in (first, last) if path is not None]
    for candidate in candidates:
        comparable = [*references, *selected]
        is_distinct = not comparable or all(
            [
                await _video_frames_are_distinct(candidate, reference)
                for reference in comparable
            ]
        )
        if is_distinct:
            selected.append(candidate)
        if len(selected) >= 3:
            break
    return selected


async def _video_frames_are_distinct(left: Path, right: Path) -> bool:
    try:
        from PIL import Image, ImageChops, ImageStat
    except Exception:  # pragma: no cover - Pillow is a declared runtime dependency
        return True

    def _compare() -> bool:
        with Image.open(left) as left_image, Image.open(right) as right_image:
            normalized_left = left_image.convert("L").resize((64, 64))
            normalized_right = right_image.convert("L").resize((64, 64))
            difference = ImageChops.difference(normalized_left, normalized_right)
            return float(ImageStat.Stat(difference).mean[0]) >= 12.0

    try:
        return await asyncio.to_thread(_compare)
    except OSError:
        return True


def _video_frame_seconds(duration: Any) -> tuple[float, float, list[float]]:
    try:
        value = float(duration)
    except (TypeError, ValueError):
        value = 12.0
    value = max(value, 1.0)
    edge_offset = min(0.1, value / 4)
    first = edge_offset
    last = max(value - edge_offset, edge_offset)
    result: list[float] = []
    for ratio in (0.2, 0.35, 0.5, 0.65, 0.8):
        second = min(max(value * ratio, first), last)
        if second not in result and second not in {first, last}:
            result.append(second)
    return first, last, result


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
