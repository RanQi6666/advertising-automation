import json

import pytest

from backend.app.db.models.copy_draft import CopyDraft
from backend.app.integrations.image.volcengine_provider import _prompt_from_brief
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.integrations.llm.openai_provider import OpenAILLMProvider
from backend.app.schemas.ai import ImageBrief


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
