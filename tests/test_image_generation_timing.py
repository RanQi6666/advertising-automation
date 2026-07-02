import logging

from backend.app.services.image_generation_timing import image_generation_timer


def test_image_generation_timer_logs_success(caplog) -> None:
    caplog.set_level(logging.INFO, logger="backend.app.services.image_generation_timing")

    with image_generation_timer(
        task_id="task-1",
        draft_id="draft-1",
        campaign_id="campaign-1",
        image_index=3,
        keyframe_group=2,
        keyframe_role="first_frame",
        stage="provider_request",
        provider="gateway",
        model="gpt-image-2",
    ) as finish:
        duration_ms = finish(status="succeeded")

    assert duration_ms >= 0
    record = caplog.records[-1]
    assert record.message == "image_generation_timing"
    assert record.image_generation["task_id"] == "task-1"
    assert record.image_generation["stage"] == "provider_request"
    assert record.image_generation["status"] == "succeeded"
    assert record.image_generation["duration_ms"] >= 0


def test_image_generation_timer_logs_failure(caplog) -> None:
    caplog.set_level(logging.INFO, logger="backend.app.services.image_generation_timing")

    try:
        with image_generation_timer(stage="download", image_index=4):
            raise RuntimeError("download failed")
    except RuntimeError:
        pass

    record = caplog.records[-1]
    assert record.image_generation["stage"] == "download"
    assert record.image_generation["status"] == "failed"
    assert record.image_generation["error_message"] == "download failed"
