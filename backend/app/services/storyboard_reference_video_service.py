from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError, ProviderError
from backend.app.db.models.video_asset import VideoAsset
from backend.app.schemas.ai import (
    ReferenceVideoAnalysis,
    ReferenceVideoFrame,
    TimelineAdaptationBeat,
    TimelineAdaptationPlan,
)
from backend.app.schemas.external_ai_generation import ExternalAIReferenceVideo
from backend.app.services.safe_public_http import (
    SafePublicHTTPError,
    download_public_http_file,
    extension_from_url_or_content_type,
)
from backend.app.services.video_storage_service import VideoStorageService

_VIDEO_SUFFIXES = {".mp4", ".mov", ".webm"}
_SUPPORTED_VIDEO_PROBE_FORMATS = {"mp4", "mov", "webm", "matroska"}
_FINAL_REFERENCE_FRAME_SEEK_MARGIN_SECONDS = 0.05


@dataclass(frozen=True)
class PreparedReferenceVideo:
    duration_seconds: float
    sample_interval_seconds: float
    frames: list[ReferenceVideoFrame]
    working_dir: Path


def adapt_reference_behavior_timeline(
    reference: ReferenceVideoAnalysis,
    *,
    target_duration_seconds: float,
) -> TimelineAdaptationPlan:
    """Convert reference-relative behavior beats into target-generation time windows.

    Reference seconds describe observed timing only.  The returned windows are a fresh,
    non-linear target timeline: causal beats retain their order and readability.  A
    reference overlay visible at source end remains observation evidence only; the director
    overlay lifecycle plan decides whether it belongs in the generated final frame.
    """
    if target_duration_seconds <= 0:
        raise ValueError("target_duration_seconds must be greater than zero")

    graph = reference.behavior_graph
    source_beats = sorted(
        graph.beats if graph is not None else [],
        key=lambda beat: (beat.reference_start_second, beat.reference_end_second, beat.beat_id),
    )
    if not source_beats:
        return TimelineAdaptationPlan(
            reference_duration_seconds=reference.duration_seconds,
            target_duration_seconds=target_duration_seconds,
        )

    persistent_beats = [
        beat
        for beat in source_beats
        if beat.behavior_type == "overlay" and beat.must_remain_visible_until_final
    ]
    sequential_beats = [beat for beat in source_beats if beat not in persistent_beats]
    risks: list[str] = []
    target_by_id: dict[str, TimelineAdaptationBeat] = {}

    if sequential_beats:
        minimum_total = sum(
            beat.minimum_readable_duration_seconds for beat in sequential_beats
        )
        reference_weight_total = sum(
            max(0.001, beat.reference_end_second - beat.reference_start_second)
            for beat in sequential_beats
        )
        if minimum_total > target_duration_seconds:
            risks.append(
                "Target duration is shorter than the combined readable minimum for the "
                "reference behavior chain; preserve causal order and review the render."
            )
            scale = target_duration_seconds / minimum_total
            durations = [
                beat.minimum_readable_duration_seconds * scale for beat in sequential_beats
            ]
        else:
            spare = target_duration_seconds - minimum_total
            durations = [
                beat.minimum_readable_duration_seconds
                + spare
                * (max(0.001, beat.reference_end_second - beat.reference_start_second)
                   / reference_weight_total)
                for beat in sequential_beats
            ]

        cursor = 0.0
        for index, (beat, duration) in enumerate(zip(sequential_beats, durations, strict=True)):
            start = cursor
            end = (
                target_duration_seconds
                if index == len(sequential_beats) - 1
                else cursor + duration
            )
            for dependency_id in beat.depends_on:
                dependency = target_by_id.get(dependency_id)
                if dependency is not None and start < dependency.target_end_second:
                    shift = dependency.target_end_second - start
                    start += shift
                    end += shift
            if end > target_duration_seconds:
                end = target_duration_seconds
                start = min(start, max(0.0, end - min(duration, end)))
            target_by_id[beat.beat_id] = TimelineAdaptationBeat(
                beat_id=beat.beat_id,
                description=beat.description,
                target_start_second=start,
                target_end_second=end,
                importance=beat.importance,
                depends_on=beat.depends_on,
                locked_text=beat.locked_text,
                adaptation_instruction=(
                    "Keep this causal beat readable in the target timeline; use its target "
                    "window rather than copying the observed reference seconds."
                ),
            )
            cursor = end

    for beat in persistent_beats:
        relative_start = min(
            1.0,
            max(0.0, beat.reference_start_second / reference.duration_seconds),
        )
        start = relative_start * target_duration_seconds
        for dependency_id in beat.depends_on:
            dependency = target_by_id.get(dependency_id)
            if dependency is not None:
                start = max(start, dependency.target_end_second)
        if start >= target_duration_seconds:
            start = max(0.0, target_duration_seconds - min(
                beat.minimum_readable_duration_seconds,
                target_duration_seconds,
            ))
        target_by_id[beat.beat_id] = TimelineAdaptationBeat(
            beat_id=beat.beat_id,
            description=beat.description,
            target_start_second=start,
            target_end_second=target_duration_seconds,
            importance=beat.importance,
            depends_on=beat.depends_on,
            must_remain_visible_until_final=False,
            locked_text=beat.locked_text,
            adaptation_instruction=(
                "Treat source-end visibility as observed reference evidence only. Preserve "
                "the exact locked text when selected, but let the director overlay_lifecycle_plan "
                "decide whether it appears, is replaced, is omitted, or persists into the "
                "generated final frame."
            ),
        )

    return TimelineAdaptationPlan(
        reference_duration_seconds=reference.duration_seconds,
        target_duration_seconds=target_duration_seconds,
        beats=[target_by_id[beat.beat_id] for beat in source_beats],
        adaptation_risks=risks,
    )


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
            change_timestamps: list[float] = []
            if self.settings.storyboard_reference_video_adaptive_sampling_enabled:
                try:
                    change_timestamps = await _detect_reference_video_change_timestamps(
                        source_path,
                        threshold=(
                            self.settings.storyboard_reference_video_scene_change_threshold
                        ),
                        max_candidates=(
                            self.settings.storyboard_reference_video_scene_change_max_candidates
                        ),
                        timeout_seconds=(
                            self.settings.storyboard_reference_video_ffmpeg_timeout_seconds
                        ),
                    )
                except ProviderError:
                    # Change detection enriches evidence only. The proven baseline sampler
                    # must remain available when FFmpeg or the source cannot expose scene scores.
                    change_timestamps = []
            selected_timestamps = _adaptive_reference_frame_timestamps(
                duration_seconds=duration_seconds,
                interval_seconds=interval,
                change_timestamps=change_timestamps,
                max_frame_count=(
                    self.settings.storyboard_reference_video_adaptive_max_frames
                ),
            )
            frames: list[ReferenceVideoFrame] = []
            for index, (timestamp_seconds, selection_reason) in enumerate(selected_timestamps):
                destination = working_dir / f"reference_frame_{index:03d}.jpg"
                extracted = await _extract_reference_video_frame(
                    source_path,
                    destination,
                    timestamp_seconds,
                    is_final=(
                        selection_reason == "ending"
                        and timestamp_seconds == round(duration_seconds, 6)
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
                        selection_reason=selection_reason,
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


def _adaptive_reference_frame_timestamps(
    *,
    duration_seconds: float,
    interval_seconds: float,
    change_timestamps: list[float],
    max_frame_count: int,
) -> list[tuple[float, str]]:
    """Merge baseline coverage with bounded, evidence-rich scene-change samples."""
    if max_frame_count < 2:
        raise AppError("Adaptive reference video sampling requires at least two frames.")

    duration = round(float(duration_seconds), 6)
    interval = float(interval_seconds)
    baseline = _reference_frame_timestamps(duration, interval)
    candidates: list[tuple[float, str]] = [
        (
            timestamp,
            "opening"
            if index == 0
            else "ending"
            if index == len(baseline) - 1
            else "baseline",
        )
        for index, timestamp in enumerate(baseline)
    ]
    change_merge_tolerance = max(0.01, min(0.1, interval / 10))
    accepted_changes: list[float] = []

    for timestamp in sorted(change_timestamps):
        try:
            normalized = round(float(timestamp), 6)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(normalized) or normalized <= 0 or normalized >= duration:
            continue
        if any(
            abs(normalized - existing) <= change_merge_tolerance
            for existing in accepted_changes
        ):
            continue
        accepted_changes.append(normalized)
        candidates.append((normalized, "high_change"))

    candidates.sort(key=lambda item: item[0])
    if len(candidates) <= max_frame_count:
        return candidates

    anchors = [item for item in candidates if item[1] in {"opening", "ending"}]
    high_change = [item for item in candidates if item[1] == "high_change"]
    baseline_middle = [item for item in candidates if item[1] == "baseline"]
    retained: list[tuple[float, str]] = [*anchors]
    for pool in (high_change, baseline_middle):
        for item in pool:
            if len(retained) >= max_frame_count:
                break
            retained.append(item)
        if len(retained) >= max_frame_count:
            break
    return sorted(retained, key=lambda item: item[0])


async def _detect_reference_video_change_timestamps(
    source: Path,
    *,
    threshold: float,
    max_candidates: int,
    timeout_seconds: float,
) -> list[float]:
    """Return FFmpeg scene-change timestamps without making them a task dependency."""
    if max_candidates <= 0:
        return []
    if shutil.which("ffmpeg") is None:
        raise ProviderError("ffmpeg is unavailable; reference scene changes cannot be detected.")
    if threshold <= 0 or threshold > 1:
        raise AppError("Reference video scene change threshold must be within (0, 1].")

    filter_expression = f"select=gt(scene\\,{threshold:.6f}),showinfo"
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(source),
        "-vf",
        filter_expression,
        "-an",
        "-f",
        "null",
        "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ProviderError("ffmpeg timed out detecting reference-video scene changes.") from exc
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="ignore").strip()[-200:]
        suffix = f": {detail}" if detail else ""
        raise ProviderError(f"ffmpeg could not detect reference-video scene changes{suffix}")

    timestamps: list[float] = []
    output = stderr.decode("utf-8", errors="ignore")
    for value in re.findall(r"pts_time:([0-9]+(?:\.[0-9]+)?)", output):
        timestamp = float(value)
        if math.isfinite(timestamp):
            timestamps.append(round(timestamp, 6))
    return sorted(set(timestamps))[:max_candidates]


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
        "stream=codec_name,duration:format=duration,format_name",
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
        format_duration = float(format_data.get("duration"))
        stream_duration = float(streams[0].get("duration") or 0)
        duration = stream_duration if stream_duration > 0 else format_duration
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
    seek_seconds = timestamp_seconds
    if is_final:
        seek_seconds = max(0.0, timestamp_seconds - _FINAL_REFERENCE_FRAME_SEEK_MARGIN_SECONDS)
    seek_arguments = ["-ss", f"{seek_seconds:.6f}"]
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
