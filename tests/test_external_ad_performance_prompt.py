import base64

from backend.app.integrations.llm.openai_provider import (
    _ad_performance_analysis_system_prompt,
    _ad_performance_user_content,
)


def test_ad_performance_prompt_sets_public_research_boundaries():
    prompt = _ad_performance_analysis_system_prompt()

    assert "caller-submitted verified Facebook delivery metrics" in prompt
    assert "system-collected public similar-ad creative/proxy signals" in prompt
    assert "executable optimization advice" in prompt
    assert "Never claim public sources verify CTR, CPC, CPA, purchases, revenue, or ROAS" in prompt


def test_video_keyframes_are_attached_as_local_visual_inputs_when_video_is_unsupported(tmp_path):
    frame_paths = []
    for index in range(4):
        frame = tmp_path / f"frame-{index}.jpg"
        frame.write_bytes(b"\xff\xd8\xff" + bytes([index]))
        frame_paths.append(str(frame))

    content = _ad_performance_user_content(
        {
            "creative": {
                "creative_type": "video",
                "video_url": "https://newpixel.messrocts.com/uploads/ad.mp4",
            },
            "media_summary": {
                "status": "available",
                "local_artifacts": {
                    "keyframe_paths": frame_paths,
                },
            },
        },
        supports_video_input=False,
    )

    assert isinstance(content, list)
    images = [item for item in content if item.get("type") == "image_url"]
    assert len(images) == 3
    assert all(item["image_url"]["url"].startswith("data:image/jpeg;base64,") for item in images)
    assert base64.b64decode(images[0]["image_url"]["url"].split(",", 1)[1]) == (
        b"\xff\xd8\xff\x00"
    )
    assert "local_artifacts" not in content[0]["text"]
    assert str(tmp_path) not in content[0]["text"]


def test_image_thumbnail_is_preferred_over_remote_image_url(tmp_path):
    thumbnail = tmp_path / "thumbnail.jpg"
    thumbnail.write_bytes(b"\xff\xd8\xffthumbnail")

    content = _ad_performance_user_content(
        {
            "creative": {
                "creative_type": "image",
                "image_url": "https://newpixel.messrocts.com/uploads/ad.jpg",
            },
            "media_summary": {
                "status": "available",
                "local_artifacts": {"thumbnail_path": str(thumbnail)},
            },
        }
    )

    assert isinstance(content, list)
    images = [item for item in content if item.get("type") == "image_url"]
    assert len(images) == 1
    assert images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert "https://newpixel.messrocts.com/uploads/ad.jpg" not in [
        item["image_url"]["url"] for item in images
    ]


def test_missing_local_visual_artifacts_are_skipped_safely():
    content = _ad_performance_user_content(
        {
            "creative": {"creative_type": "video", "video_url": "https://example.com/ad.mp4"},
            "media_summary": {
                "local_artifacts": {"keyframe_paths": ["Z:/does-not-exist/frame.jpg"]}
            },
        },
        supports_video_input=False,
    )

    assert isinstance(content, str)
    assert "local_artifacts" not in content
