from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from backend.app.core.errors import ProviderError

FINAL_TEXT_OVERLAY_LOCKS_BEGIN = "[FINAL_TEXT_OVERLAY_LOCKS]"
FINAL_TEXT_OVERLAY_LOCKS_END = "[/FINAL_TEXT_OVERLAY_LOCKS]"
_ALLOWED_PLACEMENTS = frozenset({"top_center", "lower_center", "bottom_center"})
_DEFAULT_PLACEMENT = "lower_center"


def extract_final_text_overlay_locks(
    storyboard_text: str | None,
    *,
    duration_seconds: float,
) -> list[dict[str, Any]]:
    """Read V2's explicit final-text locks without interpreting free-form script prose."""
    if duration_seconds <= 0:
        return []
    text = str(storyboard_text or "")
    start = text.find(FINAL_TEXT_OVERLAY_LOCKS_BEGIN)
    if start < 0:
        return []
    end = text.find(FINAL_TEXT_OVERLAY_LOCKS_END, start + len(FINAL_TEXT_OVERLAY_LOCKS_BEGIN))
    if end < 0:
        return []
    raw_json = text[start + len(FINAL_TEXT_OVERLAY_LOCKS_BEGIN) : end].strip()
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []

    normalized: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict) or str(item.get("kind") or "").strip() != "text":
            continue
        locked_text = str(item.get("text") or "").strip()
        if not locked_text:
            continue
        try:
            show_from_second = float(item.get("show_from_second", duration_seconds))
        except (TypeError, ValueError):
            show_from_second = duration_seconds
        placement = str(item.get("placement") or "").strip().lower()
        normalized.append(
            {
                "kind": "text",
                "text": locked_text,
                "show_from_second": max(0.0, min(show_from_second, duration_seconds)),
                "placement": placement if placement in _ALLOWED_PLACEMENTS else _DEFAULT_PLACEMENT,
            }
        )
    return normalized


async def apply_final_text_overlay_locks(
    video_path: Path,
    *,
    overlays: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Composite exact final text onto the saved local video after provider generation.

    A generative video provider may stylize or garble text. For an explicit V2 render lock,
    this function composes the exact text over the final part of the already-generated video.
    """
    if not overlays:
        return []
    if not video_path.is_file():
        raise ProviderError("Saved video was not found for final overlay composition.")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ProviderError("ffmpeg and ffprobe are required for final overlay composition.")

    width, height = await _video_dimensions(ffprobe, video_path)
    overlay_dir = video_path.parent / f".{video_path.stem}.final-overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    try:
        prepared: list[tuple[Path, float]] = []
        for index, overlay in enumerate(overlays, start=1):
            overlay_path = overlay_dir / f"overlay-{index}.png"
            _render_text_overlay_image(
                overlay_path,
                width=width,
                height=height,
                text=str(overlay["text"]),
                placement=str(overlay["placement"]),
            )
            prepared.append((overlay_path, float(overlay["show_from_second"])))

        output_path = video_path.with_name(f"{video_path.stem}.overlayed{video_path.suffix}")
        args: list[str] = [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video_path),
        ]
        for overlay_path, _ in prepared:
            args.extend(("-loop", "1", "-framerate", "30", "-i", str(overlay_path)))
        current_label = "0:v"
        filter_parts: list[str] = []
        for index, (_, show_from_second) in enumerate(prepared, start=1):
            next_label = f"overlayed{index}"
            enable = f"gte(t\\,{show_from_second:.3f})"
            filter_parts.append(
                f"[{current_label}][{index}:v]overlay=0:0:shortest=1:enable='{enable}'[{next_label}]"
            )
            current_label = next_label
        args.extend(
            (
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                f"[{current_label}]",
                "-map",
                "0:a?",
                "-c:v",
                "libx264",
                "-crf",
                "18",
                "-preset",
                "medium",
                "-c:a",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            )
        )
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _stdout, stderr = await process.communicate()
        if process.returncode != 0 or not output_path.is_file():
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise ProviderError(f"Final overlay composition failed: {detail or 'ffmpeg failed'}")
        output_path.replace(video_path)
        return overlays
    finally:
        shutil.rmtree(overlay_dir, ignore_errors=True)


async def _video_dimensions(ffprobe: str, video_path: Path) -> tuple[int, int]:
    process = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(video_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise ProviderError(f"Unable to inspect video for final overlay composition: {detail}")
    try:
        width_text, height_text = stdout.decode("utf-8").strip().split("x", 1)
        width, height = int(width_text), int(height_text)
    except (TypeError, ValueError) as exc:
        raise ProviderError(
            "Unable to determine video dimensions for final overlay composition."
        ) from exc
    if width <= 0 or height <= 0:
        raise ProviderError("Video dimensions must be positive for final overlay composition.")
    return width, height


def _render_text_overlay_image(
    path: Path,
    *,
    width: int,
    height: int,
    text: str,
    placement: str,
) -> None:
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font_size = max(30, min(int(width * 0.105), int(height * 0.08)))
    font = _load_overlay_font(font_size)
    text_bbox = draw.textbbox((0, 0), text, font=font, stroke_width=max(2, font_size // 18))
    text_width = text_bbox[2] - text_bbox[0]
    text_height = text_bbox[3] - text_bbox[1]
    horizontal_padding = max(24, font_size // 2)
    vertical_padding = max(16, font_size // 3)
    panel_width = min(width - 24, text_width + horizontal_padding * 2)
    panel_height = text_height + vertical_padding * 2
    panel_left = max(12, (width - panel_width) // 2)
    panel_top = _panel_top(height, panel_height, placement)
    panel_right = panel_left + panel_width
    panel_bottom = panel_top + panel_height
    radius = max(16, panel_height // 3)
    draw.rounded_rectangle(
        (panel_left, panel_top, panel_right, panel_bottom),
        radius=radius,
        fill=(42, 21, 0, 238),
        outline=(255, 213, 91, 255),
        width=max(3, font_size // 14),
    )
    text_left = (width - text_width) // 2
    text_top = panel_top + vertical_padding - text_bbox[1]
    draw.text(
        (text_left, text_top),
        text,
        font=font,
        fill=(255, 238, 172, 255),
        stroke_width=max(2, font_size // 18),
        stroke_fill=(121, 63, 0, 255),
    )
    image.save(path, "PNG")


def _panel_top(height: int, panel_height: int, placement: str) -> int:
    if placement == "top_center":
        return max(24, int(height * 0.08))
    if placement == "bottom_center":
        return max(24, min(height - panel_height - 24, int(height * 0.79)))
    return max(24, min(height - panel_height - 24, int(height * 0.66)))


def _load_overlay_font(font_size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, font_size)
        except OSError:
            continue
    return ImageFont.load_default()
