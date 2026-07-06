import json

import pytest

from backend.app.db.models.copy_draft import CopyDraft
from backend.app.integrations.image.volcengine_provider import _prompt_from_brief
from backend.app.integrations.llm.mock_provider import (
    MockLLMProvider,
    _mock_keyframe_role,
    _mock_v2_strategy_scene_visual,
)
from backend.app.integrations.llm.openai_provider import (
    OpenAILLMProvider,
    _creative_strategy_system_instruction,
)
from backend.app.schemas.ai import ImageBrief
from backend.app.services.creative_safety_prompts import (
    contains_creative_safety_risk,
    creative_safety_prompt_block,
    sanitize_creative_safety_text,
)
from backend.app.services.creative_strategy_builder import build_creative_strategy
from backend.app.services.game_creative_strategy import build_game_creative_strategy

BANNED_VISIBLE_TEXT = (
    "777",
    "Luck",
    "\u8d62\u94b1",
    "\u63d0\u73b0",
    "\u91d1\u5e01\u96e8",
    "\u8d4c\u573a\u684c\u9762",
)


def test_creative_safety_prompt_blocks_banned_visible_words() -> None:
    block = creative_safety_prompt_block()

    for banned in BANNED_VISIBLE_TEXT:
        assert banned in block
    assert "metallic GAJA logo" not in block
    assert "provided product or brand name" in block
    assert "real-money gambling" in block
    assert "deposit/recharge" in block
    assert "withdrawal" in block
    assert "guaranteed winning" in block
    assert "casino tables" in block
    assert "withdrawal UI" in block


def test_creative_safety_risk_detector_catches_text_and_visual_props() -> None:
    risky_values = (
        "GAJA777",
        "Luck badge",
        "\u8d62\u94b1 headline",
        "\u63d0\u73b0 button",
        "\u91d1\u5e01\u96e8 animation",
        "\u8d4c\u573a\u684c\u9762 background",
        "casino table",
        "cash rain",
        "withdrawal UI",
    )

    for value in risky_values:
        assert contains_creative_safety_risk(value)


def test_creative_safety_sanitizer_preserves_gaja_brand_without_numbers() -> None:
    sanitized = sanitize_creative_safety_text(
        "Dark neon GAJA777 lobby with metallic GAJA777 logo and premium cards."
    )

    assert "GAJA" in sanitized
    assert "GAJA777" not in sanitized
    assert "777" not in sanitized
    assert "metallic" in sanitized
    assert "premium cards" in sanitized


def test_creative_safety_fallback_is_not_gaja_specific() -> None:
    sanitized = sanitize_creative_safety_text("Show casino cash and withdrawal UI.")

    assert "GAJA" not in sanitized
    assert "premium app lobby" in sanitized
    assert "start action" in sanitized
    assert not contains_creative_safety_risk(sanitized)


def test_openai_creative_strategy_instruction_does_not_force_gaja_lobby_style() -> None:
    instruction = _creative_strategy_system_instruction().casefold()

    assert "for gaja_brand, use a dark premium neon game lobby" not in instruction
    assert "dark premium neon game lobby" not in instruction
    assert "dark neon app lobby" not in instruction


def test_openai_creative_strategy_instruction_requires_global_text_timing_and_vfx_library() -> None:
    instruction = _creative_strategy_system_instruction()

    assert "text_brand_timing_policy" in instruction
    assert "0-3s and 9-12s" in instruction
    assert "3-9s middle segment" in instruction
    assert "middle_vfx_policy" in instruction
    assert "select 2-3 VFX library items" in instruction
    assert "gambling_vfx_spectacle_package" in instruction
    assert "Boss or mysterious energy source is a VFX driver" in instruction
    assert (
        "Do not default every gambling creative to a physical door, gate, portal, or vault"
        in instruction
    )
    assert (
        "at most one variant may use a physical door/gate/portal/vault composition"
        in instruction
    )
    assert "portal/gate/vault opening" not in instruction
    assert "not a combat, leveling, equipment, or gameplay progression character" in instruction


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
    assert "Brand safety" not in prompt
    assert "Creative safety hard rules" in prompt
    assert "Visible text hard ban" in prompt
    assert "Game creative safety" in prompt
    assert "provided product or brand name" in prompt


def test_volcengine_image_prompt_keeps_brand_vip_and_cta_inside_safe_area() -> None:
    prompt = _prompt_from_brief(
        ImageBrief(
            image_index=1,
            title="GAJA keyframe hook",
            short_text="GAJA",
            visual_direction=(
                "first_frame image rule: show a royal fantasy boss scene with the "
                "GAJA brand lockup and VIP mark."
            ),
            size="9:16",
        )
    )

    assert "Full-frame composition" in prompt
    assert "logo, VIP mark, CTA" in prompt
    assert "inside the central safe area" in prompt
    assert "must not be cropped" in prompt
    assert "place VIP directly under the cleaned brand name" in prompt
    assert "Do not place brand text, VIP, or CTA at the bottom edge" in prompt


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
async def test_openai_image_prompt_includes_creative_safety_hard_rules(
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
                    "title": "Safe app lobby",
                    "short_text": "Start",
                    "visual_direction": "Low-text neon app lobby with abstract G mark.",
                    "size": "9:16",
                }
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Create a safe game lobby ad.",
        headline="Start",
        version=1,
        metadata_json={},
    )

    await provider.generate_image_briefs(draft=draft, count=1, size="9:16")

    system = captured["system"]
    assert isinstance(system, str)
    assert "Creative safety hard rules" in system
    assert "Visible text hard ban" in system
    assert "no visible brand-number text" in system
    for banned in BANNED_VISIBLE_TEXT:
        assert banned in system


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
    assert "metallic GAJA game hub" in system
    assert "no visible brand-number text" in system
    assert "first_frame image rule" in system
    assert "visible VIP mark directly under the cleaned brand name" in system
    assert "complete full-frame composition" in system
    assert "must stay fully inside the safe area" in system
    assert "Large cinematic brand typography is allowed" in system
    assert "safe text zone begins at least 10% below the top edge" in system
    assert "brand/VIP/CTA lockup above 45% image height" in system
    assert "oversized masthead typography" not in system
    assert (
        "epic hero, king, warrior, bird-god-style boss, giant serpent boss, "
        "or stone guardian boss"
    ) in system
    assert "last_frame image rule" in system
    assert "Start, Play Now, or Explore" in system
    assert payload["draft_metadata"]["creative_strategy"]["template_id"] == "mini_game_pool"
    assert payload["storyboard_context"]["creative_strategy"]["template_id"] == "mini_game_pool"
    payload_text = json.dumps(payload, ensure_ascii=False)
    assert "GAJA777" not in payload_text
    assert "777" not in payload_text
    assert "Register" not in payload_text


@pytest.mark.asyncio
async def test_openai_image_brief_prompt_names_new_strategy_fields(
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
                    "title": "Boss challenge",
                    "short_text": "Play Now",
                    "visual_direction": "Show an original boss challenge.",
                    "size": "9:16",
                }
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    creative_strategy = build_creative_strategy(
        {
            "work_order_type": "game",
            "product_name": "Puzzle Quest 2",
            "country": "India",
            "brief": "Create a cinematic game ad with a playable boss challenge.",
            "reference_signal_pack": {
                "source": "manual_reference_video_analysis",
                "rhythm_bias": ["0-3s challenge reveal", "3-9s player retry"],
            },
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Beat the boss challenge.",
        headline="Play Now",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    await provider.generate_image_briefs(
        draft=draft,
        count=1,
        size="9:16",
        storyboard_context={"creative_strategy": creative_strategy},
    )

    system = captured["system"]
    payload = captured["payload"]
    assert isinstance(system, str)
    assert isinstance(payload, dict)
    assert "brand_profile" in system
    assert "style_pack_id" in system
    assert "country_overlay" in system
    assert "boss_guidance" in system
    assert "reference_signal_pack" in system
    strategy_payload = payload["draft_metadata"]["creative_strategy"]
    assert strategy_payload["style_pack_id"] == "game/IN/boss_challenge_fantasy"
    assert strategy_payload["boss_guidance"]["role"] == "playable challenge obstacle"


@pytest.mark.asyncio
async def test_openai_image_brief_prompt_carries_country_concepts_and_layout_rules(
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
                    "title": "Epic GAJA hook",
                    "short_text": "Start",
                    "visual_direction": "Epic CG GAJA scene with safe text layout.",
                    "size": "9:16",
                }
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "country": "\u5370\u5ea6",
            "event_name": "\u9996\u5145",
            "media": "fb",
            "audience": "\u5e74\u9f8418-65",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Safe GAJA ad copy.",
        headline="Start",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    await provider.generate_image_briefs(
        draft=draft,
        count=6,
        size="9:16",
        storyboard_context={
            "keyframe_plan": {
                "mode": "video_keyframe_variants",
                "variant_count": 3,
                "frames_per_variant": 2,
                "video_duration_seconds": 12,
            },
            "creative_strategy": creative_strategy,
        },
    )

    system = captured["system"]
    payload = captured["payload"]
    assert isinstance(system, str)
    assert isinstance(payload, dict)
    assert "country_style_pack" in system
    assert "visual_concepts" in system
    assert "text_layout_rules" in system
    assert "first_three_seconds" in system
    assert "safe area" in system
    assert "auto-fit" in system
    assert "no overflow" in system
    assert "map keyframe group 1/2/3 to visual_concepts 1/2/3" in system
    assert "creative_strategy" not in payload["storyboard_context"]
    payload_text = json.dumps(payload, ensure_ascii=False).casefold()
    assert "india_mythic_neon_lobby" not in payload_text
    assert "premium neon" not in payload_text
    assert "dark neon" not in payload_text


@pytest.mark.asyncio
async def test_openai_image_brief_payload_preserves_landing_visual_reference(
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
                    "title": "Premium lobby",
                    "short_text": "Register",
                    "visual_direction": "Dark neon GAJA777 lobby with premium game cards.",
                    "size": "9:16",
                }
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "landing_page": {
                "extracted_data": {
                    "visual_reference": {
                        "source": "reference_image",
                        "status": "analyzed",
                        "palette": ["near-black navy background"],
                        "surface_style": ["dark premium mobile game lobby"],
                        "video_recipe": {
                            "duration_seconds": 12,
                            "beats": ["0-2s: dark neon GAJA777 lobby hook with premium cards"],
                        },
                    }
                }
            },
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="GAJA777 ad copy.",
        headline="Register",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    await provider.generate_image_briefs(draft=draft, count=1, size="9:16")

    payload = captured["payload"]
    assert isinstance(payload, dict)
    payload_text = json.dumps(payload, ensure_ascii=False)
    assert "GAJA777" not in payload_text
    assert "777" not in payload_text
    assert "Register" not in payload_text
    assert "dark premium mobile game lobby" not in payload_text
    assert "dark neon" not in payload_text
    assert "creative_strategy" not in payload["draft_metadata"]
    assert "landing visual reference" in captured["system"]


@pytest.mark.asyncio
async def test_openai_image_brief_payload_keeps_v2_strategy_image_context(
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
                    "title": "Busy-day routine",
                    "short_text": "Simple daily glow",
                    "visual_direction": "Serum used in a bright city routine scene.",
                    "size": "1:1",
                }
            ]
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "ecommerce",
        "market_context": {"country_code": "SG", "language": "English"},
        "audience_lens": {"age_range": "25-34", "gender": "Female"},
        "image_guidance": {
            "composition": "Show a realistic daily routine with one clear benefit cue.",
            "visual_hooks": ["bathroom counter close-up", "humid city commute", "clean serum drop"],
        },
        "raw_content": "SHOULD NOT LEAK",
    }
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Busy-day skincare made simple.",
        headline="Simple daily glow",
        version=1,
        metadata_json={"creative_strategy": strategy},
    )

    await provider.generate_image_briefs(draft=draft, count=1, size="1:1")

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert "creative_strategy.v2" in captured["system"]
    strategy_payload = payload["draft_metadata"]["creative_strategy"]
    assert strategy_payload["image_guidance"]["visual_hooks"]
    assert strategy_payload["market_context"]["country_code"] == "SG"
    assert strategy_payload["audience_lens"]["age_range"] == "25-34"
    assert "SHOULD NOT LEAK" not in json.dumps(payload, ensure_ascii=False)


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


def test_mock_keyframe_role_defaults_to_first_frame_without_plan() -> None:
    assert _mock_keyframe_role(1, None) == "first_frame"
    assert _mock_keyframe_role(2, None) == "first_frame"


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
    assert "metallic GAJA game hub" in briefs[1].visual_direction
    assert "no visible brand-number text" in briefs[1].visual_direction


@pytest.mark.asyncio
async def test_mock_image_briefs_use_premium_gaja_brand_direction() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="GAJA777 ad copy.",
        headline="Register",
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

    assert "dark neon app lobby" not in briefs[0].visual_direction
    assert "metallic GAJA wordmark" not in briefs[0].visual_direction
    assert "no visible brand-number text" not in briefs[0].visual_direction
    assert "premium neon game lobby" not in briefs[1].visual_direction
    assert briefs[0].short_text == "Start"
    brief_text = " ".join(brief.visual_direction for brief in briefs).lower()
    for risky_term in (
        "casino",
        "slot",
        "jackpot",
        "cash",
        "coin",
        "money",
        "recharge",
        "premium game cards",
        "card carousel",
        "end card",
        "dark neon",
        "premium neon",
        "game lobby",
    ):
        assert risky_term not in brief_text


@pytest.mark.asyncio
async def test_mock_image_briefs_map_keyframe_groups_to_country_visual_concepts() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "country": "India",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Create a safe epic GAJA game-world ad.",
        headline="Start",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    briefs = await provider.generate_image_briefs(
        draft=draft,
        count=6,
        size="9:16",
        storyboard_context={
            "keyframe_plan": {
                "mode": "video_keyframe_variants",
                "variant_count": 3,
                "frames_per_variant": 2,
                "video_duration_seconds": 12,
            },
            "creative_strategy": creative_strategy,
        },
    )

    directions = [brief.visual_direction for brief in briefs]
    combined = " ".join(directions)
    assert "india_epic_guardian" not in combined
    assert "india_royal_portal" not in combined
    assert "india_mythic_neon_lobby" not in combined
    assert "original Indian epic guardian" not in combined
    assert "premium neon" not in combined.lower()
    assert "dark neon" not in combined.lower()
    for banned in BANNED_VISIBLE_TEXT:
        assert banned not in combined
    for risky_term in ("casino", "slot", "jackpot", "cash", "coin", "recharge"):
        assert risky_term not in combined.lower()


@pytest.mark.asyncio
async def test_mock_image_briefs_use_low_text_gaja_direction() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Create a safe game lobby ad.",
        headline="Start",
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

    combined = " ".join(brief.visual_direction for brief in briefs)
    assert "metallic GAJA" not in combined
    assert "no visible brand-number text" not in combined
    assert "dark neon" not in combined.lower()
    assert "premium neon" not in combined.lower()
    for banned in BANNED_VISIBLE_TEXT:
        assert banned not in combined


@pytest.mark.asyncio
async def test_mock_image_briefs_filter_risky_visual_reference_strings() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "landing_page": {
                "extracted_data": {
                    "visual_reference": {
                        "source": "manual_reference",
                        "status": "provided",
                        "surface_style": [
                            "brushed metal panels",
                            "casino floor lighting",
                            "slot machine reflections",
                        ],
                        "palette": [
                            "near-black navy background",
                            "cash gold gradient",
                            "money green highlights",
                        ],
                    }
                }
            },
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="GAJA777 ad copy.",
        headline="Register",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    briefs = await provider.generate_image_briefs(draft=draft, count=1, size="9:16")

    visual_direction = briefs[0].visual_direction.lower()
    assert "brushed metal panels" not in visual_direction
    assert "near-black navy background" not in visual_direction
    assert "casino floor lighting" not in visual_direction
    assert "slot machine reflections" not in visual_direction
    assert "cash gold gradient" not in visual_direction
    assert "money green highlights" not in visual_direction


@pytest.mark.asyncio
async def test_mock_image_briefs_include_v2_strategy_direction() -> None:
    provider = MockLLMProvider()
    strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "ecommerce",
        "market_context": {"country_code": "SG", "language": "English"},
        "audience_lens": {"age_range": "25-34", "gender": "Female"},
        "image_guidance": {
            "visual_hooks": ["bathroom counter close-up", "clean serum drop", "humid city commute"]
        },
    }
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Busy-day skincare made simple.",
        headline="Simple daily glow",
        version=1,
        metadata_json={"creative_strategy": strategy},
    )

    briefs = await provider.generate_image_briefs(draft=draft, count=1, size="1:1")

    assert "creative_strategy.v2 ecommerce visual direction" in briefs[0].visual_direction
    assert "bathroom counter close-up" in briefs[0].visual_direction


@pytest.mark.asyncio
async def test_mock_game_image_briefs_include_market_game_style_pack() -> None:
    provider = MockLLMProvider()
    strategy = build_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "country": "India",
            "work_order": {
                "parsed_fields": {
                    "gender": "Female",
                    "age_min": 25,
                    "age_max": 34,
                }
            },
            "brief": "Create a cinematic gameplay challenge ad.",
        }
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Try a cinematic game challenge.",
        headline="Start",
        version=1,
        metadata_json={"creative_strategy": strategy},
    )

    briefs = await provider.generate_image_briefs(draft=draft, count=1, size="9:16")

    visual_direction = briefs[0].visual_direction
    assert "creative_strategy.v2 game visual direction" in visual_direction
    assert "culture-inspired epic fantasy" in visual_direction
    assert "cinematic RPG progression" in visual_direction
    assert "player action:" in visual_direction
    assert "real deity names" in visual_direction
    for banned in ("Ganesha", "Shiva", "Krishna", "casino", "slot", "jackpot", "cash"):
        assert banned.lower() not in visual_direction.lower()


@pytest.mark.asyncio
async def test_mock_v2_strategy_defaults_missing_vertical_to_ecommerce() -> None:
    provider = MockLLMProvider()
    strategy = {
        "schema_version": "creative_strategy.v2",
        "image_guidance": {"visual_hooks": ["desk routine"]},
    }
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Make the daily setup easier.",
        headline="Simple daily use",
        version=1,
        metadata_json={"creative_strategy": strategy},
    )

    briefs = await provider.generate_image_briefs(draft=draft, count=1, size="1:1")
    scene_visual = _mock_v2_strategy_scene_visual(strategy, 0, 3, "Demo Product")

    assert "creative_strategy.v2 ecommerce visual direction" in briefs[0].visual_direction
    assert "pain point scene" in scene_visual
    assert "unknown" not in briefs[0].visual_direction.casefold()
