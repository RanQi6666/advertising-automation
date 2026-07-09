import pytest

from backend.app.core.errors import ProviderError
from backend.app.db.models.enums import VideoStatus
from backend.app.integrations.video.base import VideoGenerationRequest, VideoSourceImage
from backend.app.integrations.video.placeholder_provider import PlaceholderVideoProvider
from backend.app.integrations.video.volcengine_provider import (
    VolcengineVideoProvider,
    _unwrap_task_response,
)
from backend.app.services.video_service import _provider_status_to_video_status


def test_volcengine_video_payload_uses_seedance_task_schema() -> None:
    provider = VolcengineVideoProvider(
        api_key="test-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="ep-20260611001554-nwgqk",
        resolution="720p",
        image_mode="first_last_frame",
        min_duration_seconds=4,
        max_duration_seconds=12,
        max_reference_images=2,
        generate_audio=False,
        watermark=False,
        return_last_frame=False,
        execution_expires_after=172800,
        priority=0,
        safety_identifier="test-user",
    )

    payload = provider._build_payload(
        VideoGenerationRequest(
            prompt="Create a direct-response ad video.",
            source_images=[
                VideoSourceImage(id="asset-1", url="https://example.com/1.png"),
                VideoSourceImage(id="asset-2", url="https://example.com/2.png"),
            ],
            duration_seconds=12,
            aspect_ratio="9:16",
        )
    )

    assert payload["model"] == "ep-20260611001554-nwgqk"
    assert payload["ratio"] == "9:16"
    assert payload["duration"] == 12
    assert payload["generate_audio"] is False
    assert payload["watermark"] is False
    assert payload["content"][0] == {
        "type": "text",
        "text": "Create a direct-response ad video.",
    }
    assert payload["content"][1]["role"] == "first_frame"
    assert payload["content"][2]["role"] == "last_frame"
    assert payload["content"][2]["image_url"]["url"] == "https://example.com/2.png"


def test_volcengine_video_payload_rejects_unsupported_seedance_duration() -> None:
    provider = VolcengineVideoProvider(
        api_key="test-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="ep-20260611001554-nwgqk",
        resolution="720p",
        image_mode="first_last_frame",
        min_duration_seconds=4,
        max_duration_seconds=12,
        max_reference_images=2,
        generate_audio=False,
        watermark=False,
        return_last_frame=False,
        execution_expires_after=172800,
        priority=0,
    )

    with pytest.raises(ProviderError):
        provider._build_payload(
            VideoGenerationRequest(
                prompt="Create a direct-response ad video.",
                source_images=[VideoSourceImage(id="asset-1", url="https://example.com/1.png")],
                duration_seconds=15,
                aspect_ratio="9:16",
            )
        )


def test_volcengine_video_payload_rejects_too_many_reference_images() -> None:
    provider = VolcengineVideoProvider(
        api_key="test-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="ep-20260611001554-nwgqk",
        resolution="720p",
        image_mode="first_last_frame",
        min_duration_seconds=4,
        max_duration_seconds=12,
        max_reference_images=2,
        generate_audio=False,
        watermark=False,
        return_last_frame=False,
        execution_expires_after=172800,
        priority=0,
    )

    with pytest.raises(ProviderError):
        provider._build_payload(
            VideoGenerationRequest(
                prompt="Create a direct-response ad video.",
                source_images=[
                    VideoSourceImage(id=f"asset-{index}", url=f"https://example.com/{index}.png")
                    for index in range(5)
                ],
                duration_seconds=12,
                aspect_ratio="9:16",
            )
        )


def test_provider_status_maps_to_internal_video_status() -> None:
    assert _provider_status_to_video_status("queued") == VideoStatus.GENERATING.value
    assert _provider_status_to_video_status("running") == VideoStatus.GENERATING.value
    assert _provider_status_to_video_status("succeeded") == VideoStatus.GENERATED.value
    assert _provider_status_to_video_status("failed") == VideoStatus.FAILED.value
    assert _provider_status_to_video_status("expired") == VideoStatus.FAILED.value


def test_volcengine_query_response_matches_requested_item_from_items_payload() -> None:
    response = {
        "total": 2,
        "items": [
            {
                "id": "cgt-other",
                "status": "succeeded",
                "content": {"video_url": "https://example.com/wrong.mp4"},
            },
            {
                "id": "cgt-target",
                "status": "succeeded",
                "content": {"video_url": "https://example.com/video.mp4"},
            },
        ],
    }

    task = _unwrap_task_response(response, "cgt-target")

    assert task["id"] == "cgt-target"
    assert task["status"] == "succeeded"
    assert task["content"]["video_url"] == "https://example.com/video.mp4"


def test_volcengine_query_response_rejects_items_payload_without_requested_item() -> None:
    response = {
        "total": 1,
        "items": [
            {
                "id": "cgt-other",
                "status": "succeeded",
                "content": {"video_url": "https://example.com/wrong.mp4"},
            }
        ],
    }

    with pytest.raises(ProviderError, match="cgt-target"):
        _unwrap_task_response(response, "cgt-target")


@pytest.mark.asyncio
async def test_volcengine_get_generation_status_uses_task_id_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = VolcengineVideoProvider(
        api_key="test-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="ep-20260611001554-nwgqk",
        resolution="720p",
        image_mode="first_last_frame",
        min_duration_seconds=4,
        max_duration_seconds=12,
        max_reference_images=2,
        generate_audio=False,
        watermark=False,
        return_last_frame=False,
        execution_expires_after=172800,
        priority=0,
    )
    calls: list[tuple[str, str, dict]] = []

    provider_job_id = "cgt-target/with space"

    async def fake_request(method: str, url: str, **kwargs: object) -> dict:
        calls.append((method, url, kwargs))
        return {
            "id": provider_job_id,
            "status": "succeeded",
            "content": {"video_url": "https://example.com/correct.mp4"},
        }

    monkeypatch.setattr(provider, "_request", fake_request)

    status = await provider.get_generation_status(provider_job_id)

    assert calls == [
        (
            "GET",
            "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/"
            "cgt-target%2Fwith%20space",
            {},
        )
    ]
    assert status.provider_job_id == provider_job_id
    assert status.provider_status == "succeeded"
    assert status.video_url == "https://example.com/correct.mp4"


@pytest.mark.asyncio
async def test_placeholder_video_provider_start_and_refresh() -> None:
    provider = PlaceholderVideoProvider()
    started = await provider.start_generation(
        VideoGenerationRequest(
            prompt="Create a video.",
            source_images=[VideoSourceImage(id="asset-1", url="https://example.com/1.png")],
            duration_seconds=12,
            aspect_ratio="1:1",
        )
    )
    status = await provider.get_generation_status(started.provider_job_id)

    assert started.provider_status == "queued"
    assert status.provider_status == "succeeded"
    assert status.video_url and status.video_url.endswith(".mp4")
