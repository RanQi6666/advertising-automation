from __future__ import annotations

import asyncio
import base64
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError, ProviderError
from backend.app.db.models.video_asset import VideoAsset
from backend.app.schemas.ai import ReferenceVideoFrame
from backend.app.schemas.external_ai_generation import ExternalAIReferenceVideo
from backend.app.services.safe_public_http import (
    SafePublicHTTPError,
    download_public_http_file,
    extension_from_url_or_content_type,
)
from backend.app.services.video_storage_service import VideoStorageService

_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
_SUPPORTED_VIDEO_PROBE_FORMATS = {"mp4", "mov", "webm", "matroska"}


@dataclass(frozen=True)
class PreparedReferenceVideo:
    duration_seconds: float
    sample_interval_seconds: float
    frames: list[ReferenceVideoFrame]
    working_dir: Path


class StoryboardReferenceVideoService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.video_storage = VideoStorageService(self.settings)

    async def prepare(
        self,
        session: AsyncSession | None,
        reference_video: ExternalAIReferenceVideo,
        *,
        task_id: str,
    ) -> PreparedReferenceVideo:
        working_dir = (
            Path(self.settings.local_storage_root)
            / "storyboard-reference-analysis"
            / _safe_path_segment(task_id)
        )
        working_dir.mkdir(parents=True, exist_ok=True)
        try:
            source_path = await self._resolve_source(
                session,
                reference_video,
                working_dir=working_dir,
            )
            metadata = await _probe_reference_video(
                source_path,
                timeout_seconds=(
                    self.settings.storyboard_reference_video_ffprobe_timeout_seconds
                ),
            )
            _validate_reference_video_format(metadata)
            duration_seconds = float(metadata["duration_seconds"])
            max_duration = self.settings.storyboard_reference_video_max_duration_seconds
            if duration_seconds > max_duration:
                raise AppError(
                    f"Reference video duration {duration_seconds:.2f}s exceeds the "
                    f"{max_duration} second limit."
                )

            interval = self.settings.storyboard_reference_video_sample_interval_seconds
            frames: list[ReferenceVideoFrame] = []
            timestamps = _reference_frame_timestamps(duration_seconds, interval)
            for index, timestamp_seconds in enumerate(timestamps):
                destination = working_dir / f"reference_frame_{index:03d}.jpg"
                extracted = await _extract_reference_video_frame(
                    source_path,
                    destination,
                    timestamp_seconds,
                    is_final=(
                        index == len(timestamps) - 1
                        and timestamp_seconds == duration_seconds
                    ),
                    width=self.settings.storyboard_reference_video_frame_width,
                    jpeg_quality=self.settings.storyboard_reference_video_jpeg_quality,
                    timeout_seconds=(
                        self.settings.storyboard_reference_video_ffmpeg_timeout_seconds
                    ),
                )
                frames.append(
                    ReferenceVideoFrame(
                        timestamp_seconds=timestamp_seconds,
                        image_url=_jpeg_data_url(extracted),
                    )
                )

            return PreparedReferenceVideo(
                duration_seconds=duration_seconds,
                sample_interval_seconds=interval,
                frames=frames,
                working_dir=working_dir,
            )
        except Exception:
            await asyncio.to_thread(shutil.rmtree, working_dir, True)
            raise

    async def cleanup(self, prepared: PreparedReferenceVideo) -> None:
        await asyncio.to_thread(shutil.rmtree, prepared.working_dir, True)

    async def _resolve_source(
        self,
        session: AsyncSession | None,
        reference_video: ExternalAIReferenceVideo,
        *,
        working_dir: Path,
    ) -> Path:
        if reference_video.source_type == "uploaded_asset":
            return self.video_storage.path_for_uploaded_reference_video(
                reference_video.upload_asset_id
            )
        if reference_video.source_type == "video_asset":
            if session is None:
                raise AppError("Database session is required for a VideoAsset reference.")
            asset = await session.get(VideoAsset, reference_video.video_asset_id)
            if asset is None:
                raise AppError("Reference VideoAsset was not found.")
            local_path = self.video_storage.path_for_storage_key(asset.storage_key)
            if local_path is not None:
                return local_path
            if not asset.url:
                raise AppError("Reference VideoAsset has no available video source.")
            return await self._download_url(asset.url, working_dir)
        return await self._download_url(reference_video.video_url, working_dir)

    async def _download_url(self, url: str, working_dir: Path) -> Path:
        suffix = extension_from_url_or_content_type(url, None, ".mp4").lower()
        if suffix not in _VIDEO_SUFFIXES:
            suffix = ".mp4"
        destination = working_dir / f"source{suffix}"
        try:
            downloaded = await download_public_http_file(
                url,
                destination,
                max_bytes=self.settings.video_download_max_bytes,
                timeout_seconds=self.settings.video_download_timeout_seconds,
                allow_private_networks=(
                    self.settings.storyboard_reference_video_allow_private_hosts
                ),
            )
        except SafePublicHTTPError as exc:
            raise ProviderError(f"Failed to download reference video: {exc}") from exc
        return downloaded.path


def _reference_frame_timestamps(duration_seconds: float, interval_seconds: float) -> list[float]:
    duration = float(duration_seconds)
    interval = float(interval_seconds)
    if duration <= 0 or interval <= 0:
        raise AppError("Reference video duration and sample interval must be positive.")
    timestamps = [0.0]
    timestamp = interval
    while timestamp < duration:
        timestamps.append(round(timestamp, 6))
        timestamp += interval
    final_timestamp = round(duration, 6)
    if timestamps[-1] != final_timestamp:
        timestamps.append(final_timestamp)
    return timestamps


async def _probe_reference_video(
    source: Path,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    if shutil.which("ffprobe") is None:
        raise ProviderError("ffprobe is unavailable; reference video cannot be verified.")
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=codec_name:format=duration,format_name",
        "-of",
        "json",
        str(source),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ProviderError(
            f"ffprobe timed out after {timeout_seconds:g} seconds while verifying "
            "the reference video."
        ) from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="ignore").strip()[:200]
        suffix = f": {detail}" if detail else ""
        raise ProviderError(f"ffprobe could not verify the reference video{suffix}")
    try:
        data = json.loads(stdout.decode("utf-8"))
        format_data = data.get("format") or {}
        streams = data.get("streams") or []
        duration = float(format_data.get("duration"))
        format_name = str(format_data.get("format_name") or "")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProviderError("ffprobe returned invalid reference video metadata.") from exc
    if duration <= 0 or not format_name or not streams:
        raise ProviderError("ffprobe returned incomplete reference video metadata.")
    return {
        "duration_seconds": duration,
        "format": format_name,
        "codec": streams[0].get("codec_name"),
    }


def _validate_reference_video_format(metadata: dict[str, Any]) -> None:
    format_names = {
        item.strip().lower()
        for item in str(metadata.get("format") or "").split(",")
        if item.strip()
    }
    if not format_names.intersection(_SUPPORTED_VIDEO_PROBE_FORMATS):
        raise AppError("Reference video must be a verified MP4, MOV, or WebM file.")


async def _extract_reference_video_frame(
    source: Path,
    destination: Path,
    timestamp_seconds: float,
    *,
    is_final: bool,
    width: int,
    jpeg_quality: int,
    timeout_seconds: float,
) -> Path:
    if shutil.which("ffmpeg") is None:
        raise ProviderError("ffmpeg is unavailable; reference frames cannot be extracted.")
    seek_arguments = (
        ["-sseof", "-0.05"]
        if is_final
        else ["-ss", f"{timestamp_seconds:.6f}"]
    )
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        *seek_arguments,
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale='min({width},iw)':-2",
        "-q:v",
        str(jpeg_quality),
        str(destination),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        destination.unlink(missing_ok=True)
        raise ProviderError(
            f"ffmpeg timed out extracting reference frame at {timestamp_seconds:.2f}s."
        ) from exc
    if process.returncode != 0 or not destination.exists() or destination.stat().st_size <= 0:
        destination.unlink(missing_ok=True)
        detail = stderr.decode("utf-8", errors="ignore").strip()[-200:]
        suffix = f": {detail}" if detail else ""
        raise ProviderError(
            f"ffmpeg could not extract reference frame at {timestamp_seconds:.2f}s{suffix}"
        )
    return destination


def _jpeg_data_url(path: Path) -> str:
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError as exc:
        raise ProviderError("Extracted reference frame could not be read.") from exc
    if not encoded:
        raise ProviderError("Extracted reference frame is empty.")
    return f"data:image/jpeg;base64,{encoded}"


def _safe_path_segment(value: str) -> str:
    segment = "".join(character for character in value if character.isalnum() or character in "-_")
    if not segment:
        raise AppError("Invalid reference video task id.")
    return segment[:128]
