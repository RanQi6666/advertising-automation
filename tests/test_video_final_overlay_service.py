from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.services import video_final_overlay_service as overlay_module
from backend.app.services.video_final_overlay_service import (
    FINAL_TEXT_OVERLAY_LOCKS_BEGIN,
    FINAL_TEXT_OVERLAY_LOCKS_END,
    extract_final_text_overlay_locks,
)


def test_extract_final_text_overlay_locks_reads_exact_text_and_final_hold() -> None:
    storyboard_text = "\n".join(
        (
            "Scene 7 (9.35-10s)",
            FINAL_TEXT_OVERLAY_LOCKS_BEGIN,
            '[{"kind":"text","text":"x200,000","show_from_second":9.35,"placement":"lower_center"}]',
            FINAL_TEXT_OVERLAY_LOCKS_END,
        )
    )

    assert extract_final_text_overlay_locks(storyboard_text, duration_seconds=10) == [
        {
            "kind": "text",
            "text": "x200,000",
            "show_from_second": 9.35,
            "placement": "lower_center",
        }
    ]


def test_extract_final_text_overlay_locks_ignores_non_machine_readable_mentions() -> None:
    storyboard_text = (
        'The final reward text overlay reading exactly "x200,000" must remain visible.'
    )

    assert extract_final_text_overlay_locks(storyboard_text, duration_seconds=10) == []


def test_extract_final_text_overlay_locks_clamps_the_hold_to_the_final_frame() -> None:
    storyboard_text = "\n".join(
        (
            FINAL_TEXT_OVERLAY_LOCKS_BEGIN,
            '[{"kind":"text","text":"LEVEL UP","show_from_second":-3,"placement":"unknown"}]',
            FINAL_TEXT_OVERLAY_LOCKS_END,
        )
    )

    assert extract_final_text_overlay_locks(storyboard_text, duration_seconds=10) == [
        {
            "kind": "text",
            "text": "LEVEL UP",
            "show_from_second": 0.0,
            "placement": "lower_center",
        }
    ]


@pytest.mark.asyncio
async def test_apply_final_text_overlay_locks_stops_at_the_primary_video_end(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video_path = tmp_path / "provider-video.mp4"
    video_path.write_bytes(b"provider video")
    captured_args: list[str] = []

    class SuccessfulFfmpegProcess:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            Path(captured_args[-1]).write_bytes(b"overlayed video")
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_args[:] = [str(arg) for arg in args]
        return SuccessfulFfmpegProcess()

    async def fake_video_dimensions(_ffprobe: str, _path: Path) -> tuple[int, int]:
        return 720, 1280

    def fake_render_overlay(path: Path, **kwargs) -> None:
        path.write_bytes(b"overlay png")

    monkeypatch.setattr(overlay_module.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(overlay_module, "_video_dimensions", fake_video_dimensions)
    monkeypatch.setattr(overlay_module, "_render_text_overlay_image", fake_render_overlay)
    monkeypatch.setattr(
        overlay_module.asyncio,
        "create_subprocess_exec",
        fake_create_subprocess_exec,
    )

    await overlay_module.apply_final_text_overlay_locks(
        video_path,
        overlays=[
            {
                "kind": "text",
                "text": "x200,000",
                "show_from_second": 9.35,
                "placement": "lower_center",
            }
        ],
    )

    filter_complex = captured_args[captured_args.index("-filter_complex") + 1]
    assert "shortest=1" in filter_complex
