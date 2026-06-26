import json

import pytest

from backend.app.db.models.copy_draft import CopyDraft
from backend.app.integrations.image.volcengine_provider import _prompt_from_brief
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.integrations.llm.openai_provider import OpenAILLMProvider
from backend.app.schemas.ai import ImageBrief
from backend.app.services.brand_safety_policy import scan_brand_safety
from backend.app.services.game_creative_strategy import build_game_creative_strategy


def test_volcengine_image_prompt_is_platform_neutral_and_blocks_ui_chrome() -> None:
    prompt = _prompt_from_brief(
        ImageBrief(
            image_index=1,
            title="Benefit-led product scene",
            short_text="Try it today",
            visual_direction=(
                "Show the product in a clean Facebook placement without Meta UI, "
                "Instagram frames, or Sponsored labels."
            ),
            size="1:1",
        )
    )

    assert "Facebook" not in prompt
    assert "Meta" not in prompt
    assert "Instagram" not in prompt
    assert "Sponsored" not in prompt
    assert "独立的移动端信息流广告素材图片" in prompt
    assert "平台 Logo" in prompt
    assert "应用界面" in prompt
    assert "信息流页面截图" in prompt
    assert "点赞/评论/分享按钮" in prompt
    assert "二维码" in prompt
    assert "水印" in prompt
    assert scan_brand_safety({"prompt": prompt})["status"] == "passed"
    assert "cash" not in prompt
    assert "bank cards" not in prompt
    assert "discount stickers" not in prompt
    assert "coupons" not in prompt
    assert "casinos" not in prompt
    assert "pills" not in prompt


@pytest.mark.asyncio
async def test_openai_image_brief_prompt_forbids_platform_branding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAILLMProvider(api_key="test-key", model="test-model")
    captured: dict[str, object] = {}

    async def fake_json_completion(system: str, user: str) -> dict:
        captured["system"] = system
        captured["payload"] = json.loads(user)
        return {
            "briefs": [
                {
                    "image_index": 1,
                    "title": "Main benefit",
                    "short_text": "Try it today",
                    "visual_direction": "Product scene with clear negative space.",
                    "size": "1:1",
                }
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Ad copy body.",
        headline="Try it today",
        version=1,
        metadata_json={},
    )

    await provider.generate_image_briefs(draft=draft, count=1, size="1:1")

    system = captured["system"]
    assert isinstance(system, str)
    assert "independent creative asset" in system
    assert "not a platform feed screenshot" in system
    assert "must not mention Facebook, Meta, Instagram" in system
    assert "platform UI/chrome" in system
    assert "like/comment/share buttons" in system
    assert "QR codes" in system
    assert "watermarks" in system


@pytest.mark.asyncio
async def test_openai_image_brief_prompt_carries_game_creative_strategy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAILLMProvider(api_key="test-key", model="test-model")
    captured: dict[str, object] = {}

    async def fake_json_completion(system: str, user: str) -> dict:
        captured["system"] = system
        captured["payload"] = json.loads(user)
        return {
            "briefs": [
                {
                    "image_index": 1,
                    "title": "Mini game hook",
                    "short_text": "Play Now",
                    "visual_direction": "Mini-game challenge with GAJA hub end card.",
                    "size": "9:16",
                }
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": "\u5c0f\u6e38\u620f\u6d41\u91cf\u6c60",
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Mini game ad copy.",
        headline="Play Now",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    await provider.generate_image_briefs(
        draft=draft,
        count=1,
        size="9:16",
        storyboard_context={
            "keyframe_plan": {
                "mode": "video_keyframe_variants",
                "video_duration_seconds": 12,
            },
            "creative_strategy": creative_strategy,
        },
    )

    system = captured["system"]
    payload = captured["payload"]
    assert isinstance(system, str)
    assert isinstance(payload, dict)
    assert "creative_strategy" in system
    assert "mandatory ad-direction context" in system
    assert "first-frame hook" in system
    assert "last-frame" in system
    assert "GAJA777 game hub" in system
    assert payload["draft_metadata"]["creative_strategy"]["template_id"] == "mini_game_pool"
    assert payload["storyboard_context"]["creative_strategy"]["template_id"] == "mini_game_pool"


@pytest.mark.asyncio
async def test_mock_image_briefs_are_platform_neutral() -> None:
    provider = MockLLMProvider()
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Ad copy body.",
        headline="Try it today",
        version=1,
        metadata_json={},
    )

    briefs = await provider.generate_image_briefs(draft=draft, count=1, size="1:1")

    assert "Facebook" not in briefs[0].visual_direction
    assert "mobile feed placements" in briefs[0].visual_direction
    assert "standalone performance-ad layout" in briefs[0].visual_direction
    assert "platform logos" not in briefs[0].visual_direction


@pytest.mark.asyncio
async def test_mock_image_briefs_include_game_strategy_direction() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": "\u5c0f\u6e38\u620f\u6d41\u91cf\u6c60",
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Mini game ad copy.",
        headline="Play Now",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    briefs = await provider.generate_image_briefs(
        draft=draft,
        count=2,
        size="9:16",
        storyboard_context={
            "keyframe_plan": {
                "mode": "video_keyframe_variants",
                "frames_per_variant": 2,
                "video_duration_seconds": 12,
            },
            "creative_strategy": creative_strategy,
        },
    )

    assert "mini-game challenge" in briefs[0].visual_direction
    assert "GAJA777 game hub" in briefs[1].visual_direction
