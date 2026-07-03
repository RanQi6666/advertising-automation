import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import Settings, get_settings
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.landing_page_snapshot import LandingPageSnapshot
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.integrations.llm.openai_provider import (
    OpenAILLMProvider,
    _video_storyboard_text_system_prompt,
)
from backend.app.schemas.video import (
    VideoGenerateRequest,
    VideoStoryboardGenerateRequest,
    VideoStoryboardRewriteRequest,
)
from backend.app.services import video_service
from backend.app.services.creative_safety_prompts import creative_safety_prompt_block
from backend.app.services.creative_strategy_builder import build_creative_strategy
from backend.app.services.game_creative_strategy import build_game_creative_strategy
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.video_service import (
    VideoService,
    _ensure_storyboard_source_asset_notes,
    _prompt_with_creative_strategy,
    _redact_provider_request_payload,
    _resolve_video_source_image_url,
    _storyboard_to_prompt,
    _stream_text_with_heartbeat,
)


@pytest.mark.asyncio
async def test_mock_provider_generates_video_storyboard() -> None:
    provider = MockLLMProvider()
    campaign = Campaign(
        id="campaign-1",
        name="India TV campaign",
        product_name="India TV",
        audience_description="Men 25-45",
        metadata_json={},
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Watch live TV channels free.",
        primary_text="Watch 1000+ live TV channels free.",
        version=1,
        metadata_json={},
    )
    assets = [
        CreativeAsset(
            id="asset-1",
            campaign_id="campaign-1",
            draft_id="draft-1",
            prompt="Show live TV channels on a phone.",
            size="1:1",
            metadata_json={},
        ),
        CreativeAsset(
            id="asset-2",
            campaign_id="campaign-1",
            draft_id="draft-1",
            prompt="Show sports and movie categories.",
            size="1:1",
            metadata_json={},
        ),
    ]

    storyboard = await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=assets,
        duration_seconds=12,
        aspect_ratio="9:16",
        context={"landing_page": {"title": "Free Live TV"}},
        instructions="Use short English subtitles.",
    )

    assert storyboard.duration_seconds == 12
    assert storyboard.aspect_ratio == "9:16"
    assert len(storyboard.scenes) == 3
    assert storyboard.scenes[0].source_asset_ids == ["asset-1"]
    assert storyboard.scenes[-1].subtitle == "Download Now"


@pytest.mark.asyncio
async def test_mock_provider_uses_game_strategy_for_video_storyboard() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": "\u5c0f\u6e38\u620f\u6d41\u91cf\u6c60",
        }
    )
    campaign = Campaign(
        id="campaign-1",
        name="GAJA777 campaign",
        product_name="GAJA777",
        audience_description="India users",
        metadata_json={"creative_strategy": creative_strategy},
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Mini game ad copy.",
        primary_text="Try a quick mini-game and find more games on GAJA777.",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    storyboard = await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=[],
        duration_seconds=12,
        aspect_ratio="9:16",
        context={"creative_strategy": creative_strategy},
        instructions=None,
    )

    assert "mini-game challenge" in storyboard.scenes[0].visual
    assert "metallic GAJA game hub" in storyboard.scenes[-1].visual
    assert "no visible brand-number text" in storyboard.scenes[-1].visual
    assert storyboard.scenes[-1].subtitle in {"Start", "Play Now"}


@pytest.mark.asyncio
async def test_openai_video_storyboard_payload_sanitizes_creative_prompt_terms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAILLMProvider(api_key="test-key", model="test-model")
    captured: dict[str, object] = {}
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
                            "beats": [
                                "0-2s: dark neon GAJA777 lobby hook with premium cards",
                                "10-12s: Register / Play Now end card",
                            ],
                        },
                    }
                }
            },
        }
    )

    async def fake_json_completion(system: str, user: str) -> dict:
        captured["system"] = system
        captured["payload"] = json.loads(user)
        return {
            "duration_seconds": 12,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 4,
                    "visual": "Open with abstract G mark.",
                    "subtitle": "Start",
                    "motion": "Slow push.",
                    "voiceover": "Start exploring.",
                    "source_asset_ids": [],
                    "notes": "Safe scene.",
                }
            ],
            "rationale": "Safe low-text direction.",
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    campaign = Campaign(
        id="campaign-1",
        name="GAJA777 campaign",
        product_name="GAJA777",
        audience_description="India users",
        metadata_json={"creative_strategy": creative_strategy},
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="GAJA777 ad copy.",
        primary_text="Explore GAJA777 lobby and Register.",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=[],
        duration_seconds=12,
        aspect_ratio="9:16",
        context={"creative_strategy": creative_strategy},
        instructions="Use the GAJA777 end card but keep it safe.",
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    payload_text = json.dumps(payload, ensure_ascii=False)
    assert "GAJA777" not in payload_text
    assert "777" not in payload_text
    assert "Register" not in payload_text
    assert "metallic GAJA logo" not in payload_text
    assert "dark premium mobile game lobby" not in payload_text
    assert "dark neon" not in payload_text


@pytest.mark.asyncio
async def test_mock_provider_streams_video_storyboard_text() -> None:
    provider = MockLLMProvider()
    campaign = Campaign(
        id="campaign-1",
        name="India TV campaign",
        product_name="India TV",
        audience_description="Men 25-45",
        metadata_json={},
    )
    asset = CreativeAsset(
        id="asset-1",
        campaign_id="campaign-1",
        draft_id="draft-1",
        prompt="Show live TV channels on a phone.",
        size="1:1",
        metadata_json={},
    )

    chunks = [
        chunk
        async for chunk in provider.stream_video_storyboard_text(
            campaign=campaign,
            draft=None,
            assets=[asset],
            duration_seconds=12,
            aspect_ratio="9:16",
            context={"landing_page": {"title": "Free Live TV"}},
            instructions="Use short subtitles.",
        )
    ]

    text = "".join(chunks)
    assert len(chunks) > 1
    assert "视频分镜脚本" in text
    assert "asset-1" in text


@pytest.mark.asyncio
async def test_video_storyboard_stream_sends_heartbeat_while_text_is_pending(
    monkeypatch,
) -> None:
    monkeypatch.setattr(video_service, "VIDEO_STREAM_HEARTBEAT_SECONDS", 0.001)

    async def slow_chunks() -> AsyncIterator[str]:
        await asyncio.sleep(0.02)
        yield "Scene 1: Open with the core benefit."

    events = [
        event
        async for event in _stream_text_with_heartbeat(
            slow_chunks(),
            stage="video_storyboard_generation",
        )
    ]

    heartbeats = [event for event in events if event["type"] == "heartbeat"]
    assert heartbeats
    assert heartbeats[0]["stage"] == "video_storyboard_generation"
    assert heartbeats[0]["interval_seconds"] == 0.001
    assert {"type": "delta", "text": "Scene 1: Open with the core benefit."} in events


def test_video_generate_request_accepts_storyboard() -> None:
    payload = VideoGenerateRequest(
        campaign_id="campaign-1",
        creative_asset_ids=["asset-1"],
        draft_id="draft-1",
        prompt="Use this storyboard.",
        duration_seconds=12,
        aspect_ratio="9:16",
        storyboard=[{"scene_index": 1, "visual": "Open with the app benefit."}],
    )

    assert payload.storyboard[0]["scene_index"] == 1
    assert payload.storyboard[0]["visual"] == "Open with the app benefit."


@pytest.mark.asyncio
async def test_video_service_generates_storyboard_from_draft_without_reference_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-1",
            name="India TV campaign",
            product_name="India TV",
            audience_description="Men 25-45",
            metadata_json={},
        )
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="Show live TV clearly in a home scene.",
            headline="Live TV at home",
            metadata_json={},
        )
        session.add_all([campaign, draft])
        await session.commit()

        storyboard = await VideoService().generate_storyboard(
            session,
            VideoStoryboardGenerateRequest(
                campaign_id=campaign.id,
                creative_asset_ids=[],
                draft_id=draft.id,
                duration_seconds=12,
                aspect_ratio="9:16",
            ),
        )

    assert storyboard.draft_id == draft.id
    assert storyboard.creative_asset_ids == []
    assert storyboard.storyboard

    get_settings.cache_clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_video_service_done_text_replaces_empty_source_notes_when_assets_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProviderThatOmitsSourceIds:
        async def stream_video_storyboard_text(self, **_: object) -> AsyncIterator[str]:
            yield "Scene 1 | 0.0-2.0s\n"
            yield "Source image id notes: No source image provided.\n"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = VideoService()
    monkeypatch.setattr(
        service,
        "_llm_for_model",
        lambda _model_id: (ProviderThatOmitsSourceIds(), None),
    )

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-1",
            name="GAJA777 campaign",
            product_name="GAJA777",
            audience_description="India users",
            metadata_json={},
        )
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="Create a short game-platform video.",
            metadata_json={},
        )
        asset = CreativeAsset(
            id="asset-1",
            campaign_id=campaign.id,
            draft_id=draft.id,
            prompt="GAJA777 brand lobby reference image.",
            metadata_json={},
        )
        session.add_all([campaign, draft, asset])
        await session.commit()

        events = [
            event
            async for event in service.stream_storyboard_text(
                session,
                VideoStoryboardGenerateRequest(
                    campaign_id=campaign.id,
                    creative_asset_ids=[asset.id],
                    draft_id=draft.id,
                    duration_seconds=12,
                    aspect_ratio="9:16",
                ),
            )
        ]

    done = events[-1]
    assert done["type"] == "done"
    assert "Source image id notes: asset-1" in done["text"]
    assert "No source image provided" not in done["text"]

    await engine.dispose()


def test_video_service_replaces_no_reference_image_required_when_assets_selected() -> None:
    asset = CreativeAsset(
        id="asset-1",
        campaign_id="campaign-1",
        draft_id="draft-1",
        prompt="Approved reference frame.",
        metadata_json={},
    )

    text = (
        "Scene 1 | 0.0-2.0s\n"
        "Source image id notes: No reference image required.\n"
    )

    fixed = _ensure_storyboard_source_asset_notes(text, [asset])

    assert "Source image id notes: asset-1." in fixed
    assert "No reference image required" not in fixed


@pytest.mark.asyncio
async def test_video_context_merges_latest_landing_visual_reference_snapshot() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        creative_strategy = {
            "template_id": "gaja_brand",
            "first_frame": {"role": "hook"},
            "last_frame": {"role": "cta"},
        }
        reference = {
            "palette": ["near-black navy background"],
            "surface_style": ["dark premium mobile game lobby"],
        }
        campaign = Campaign(
            id="campaign-1",
            name="GAJA777 campaign",
            product_name="GAJA777",
            audience_description="India users",
            metadata_json={"creative_strategy": creative_strategy},
        )
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="GAJA777 ad copy.",
            version=1,
            metadata_json={"creative_strategy": creative_strategy},
        )
        session.add_all(
            [
                campaign,
                draft,
                LandingPageSnapshot(
                    campaign_id=campaign.id,
                    url="https://www.gaja777.game",
                    extracted_data={"visual_reference": reference},
                ),
            ]
        )
        await session.commit()

        context = await VideoService()._video_context(
            session,
            campaign,
            draft,
            [],
            None,
        )

    assert context["creative_strategy"]["landing_visual_reference"] == reference

    await engine.dispose()


def test_video_storyboard_prompt_includes_creative_safety_visual_guidance() -> None:
    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 4,
                "visual": "Open with a clean product scene.",
                "subtitle": "Start now",
            }
        ]
    )

    assert "Brand safety visual guidance" not in prompt
    assert "Creative safety hard rules" in prompt
    assert "Visible text hard ban" in prompt
    assert "Game creative safety" in prompt
    assert "provided product or brand name" in prompt


def test_video_storyboard_prompt_includes_creative_text_and_prop_bans() -> None:
    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 4,
                "visual": "Open with a clean neon app lobby.",
                "subtitle": "Start",
            }
        ]
    )

    assert "Creative safety hard rules" in prompt
    assert "Visible text hard ban" in prompt
    assert "Game creative safety" in prompt
    assert "provided product or brand name" in prompt
    assert "casino tables" in prompt
    assert "withdrawal UI" in prompt
    for banned in (
        "777",
        "Luck",
        "\u8d62\u94b1",
        "\u63d0\u73b0",
        "\u91d1\u5e01\u96e8",
        "\u8d4c\u573a\u684c\u9762",
    ):
        assert banned in prompt


def test_storyboard_prompt_includes_keyframe_brand_aaa_timing_rules() -> None:
    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 12,
                "visual": "Open with a GAJA challenge and resolve on a CTA.",
                "subtitle": "Play Now",
            }
        ]
    )

    assert "0-3s opening rule" in prompt
    assert (
        "epic hero, king, warrior, bird-god-style boss, giant serpent boss, "
        "or stone guardian boss"
    ) in prompt
    assert "3-9s middle VFX rule" in prompt
    assert "coin explosion effects" in prompt
    assert "divine light descent" in prompt
    assert "portal effects" in prompt
    assert "jackpot-style feedback" in prompt
    assert "boss defeat" in prompt
    assert "Score, Points, Stars, or Power" in prompt
    assert "9-12s ending rule" in prompt
    assert "Do not show cash amounts" in prompt


def test_direct_video_prompt_includes_keyframe_brand_aaa_timing_rules() -> None:
    result = _prompt_with_creative_strategy(
        "Create a 12-second GAJA challenge video.",
        None,
    )

    assert result is not None
    assert "0-3s opening rule" in result
    assert "3-9s middle VFX rule" in result
    assert "coin explosion effects" in result
    assert "divine light descent" in result
    assert "portal effects" in result
    assert "jackpot-style feedback" in result
    assert "boss defeat" in result
    assert "Score, Points, Stars, or Power" in result
    assert "9-12s ending rule" in result
    assert "Do not show cash amounts" in result


def test_video_prompt_with_existing_safety_block_preserves_prompt_text() -> None:
    prompt = f"Open on a low-text app lobby with abstract G mark.\n{creative_safety_prompt_block()}"

    result = _prompt_with_creative_strategy(prompt, None)

    assert result is not None
    assert "Open on a low-text app lobby with abstract G mark." in result
    assert creative_safety_prompt_block() in result
    assert "low-text premium neon app lobby with abstract G mark" not in result


def test_video_storyboard_prompt_includes_game_creative_strategy() -> None:
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "brief": "\u5c0f\u6e38\u620f\u6d41\u91cf\u6c60",
        }
    )

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 4,
                "visual": "Open with a playable mini-game challenge.",
                "subtitle": "Play Now",
            },
            {
                "scene_index": 3,
                "start_second": 8,
                "end_second": 12,
                "visual": "End on the GAJA777 lobby.",
                "subtitle": "Register",
            },
        ],
        creative_strategy=creative_strategy,
    )

    assert "creative_strategy: mini_game_pool" in prompt
    assert "12-second first/last-frame workflow" in prompt
    assert "first-frame hook" in prompt
    assert "last-frame" in prompt
    assert "metallic GAJA game hub" in prompt
    assert "Subtitle: Start" in prompt
    assert "Subtitle: Register" not in prompt


def test_video_storyboard_text_prompt_requires_opening_brand_without_digits() -> None:
    prompt = _video_storyboard_text_system_prompt(revision=False)

    assert "opening frame must show the project or product name" in prompt
    assert "remove digit characters from visible brand text" in prompt


def test_gaja_video_prompt_ignores_disabled_brand_template() -> None:
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 3,
                "visual": "Open with a gameplay challenge.",
                "subtitle": "Start",
            }
        ],
        creative_strategy=creative_strategy,
    )

    prompt_text = prompt.casefold()
    assert "creative_strategy: gaja_brand" not in prompt_text
    assert "opening brand rule" not in prompt_text
    assert "dark neon" not in prompt_text
    assert "premium neon" not in prompt_text
    assert "game lobby" not in prompt_text


def test_video_storyboard_prompt_includes_v2_duration_adaptive_strategy() -> None:
    creative_strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "game",
        "market_context": {"country_code": "SG", "language": "English"},
        "audience_lens": {"age_range": "25-34", "expression_style": ["short", "energetic"]},
        "topic_angle_plan": [
            {"slot": 1, "angle_type": "challenge_failure", "purpose": "Test challenge hook."}
        ],
        "video_guidance": {
            "duration_adaptive": True,
            "short_video_rules": ["One strong hook, one payoff, one CTA."],
            "medium_video_rules": ["Hook, conflict, payoff, CTA."],
            "long_video_rules": ["Full story with proof and CTA."],
            "beats_by_vertical": ["challenge", "failure", "correct move", "reward"],
        },
        "compliance_guardrails": ["Do not imply guaranteed wins."],
    }

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 6,
                "visual": "Open with a level challenge.",
                "subtitle": "Can you pass?",
            }
        ],
        creative_strategy=creative_strategy,
    )

    assert "creative_strategy: creative_strategy.v2 game" in prompt
    assert "duration-adaptive" in prompt
    assert "12-second first/last-frame workflow" not in prompt
    assert "challenge_failure" in prompt
    assert "One strong hook, one payoff, one CTA." in prompt


def test_video_storyboard_prompt_defaults_missing_v2_vertical_to_ecommerce() -> None:
    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 6,
                "visual": "Show a routine product moment.",
                "subtitle": "Make daily use easier.",
            }
        ],
        creative_strategy={
            "schema_version": "creative_strategy.v2",
            "video_guidance": {"opening": "Lead with the routine problem."},
        },
    )

    assert "creative_strategy: creative_strategy.v2 ecommerce" in prompt
    assert "unknown" not in prompt.casefold()


def test_video_storyboard_prompt_includes_builder_video_guidance() -> None:
    creative_strategy = build_creative_strategy(
        {
            "product_name": "Puzzle Quest",
            "landing_url": "https://play.example.sg/level-challenge",
            "country": "Singapore",
            "brief": "Create a level challenge game ad.",
        }
    )

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 6,
                "visual": "Open with a level challenge.",
                "subtitle": "Can you pass?",
            }
        ],
        creative_strategy=creative_strategy,
    )

    assert "Lead with a visible challenge or failed attempt." in prompt
    assert "Show progression, choice, or improvement." in prompt
    assert "Resolve with reward, unlock, or next-action payoff." in prompt


def test_video_storyboard_prompt_includes_market_game_style_pack_gameplay_process() -> None:
    creative_strategy = build_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "country": "India",
            "work_order": {
                "parsed_fields": {
                    "gender": "Male",
                    "age_min": 18,
                    "age_max": 24,
                }
            },
            "brief": "Create a cinematic game ad with a playable challenge process.",
        }
    )

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 4,
                "visual": "Open with a cinematic playable challenge.",
                "subtitle": "Start",
            },
            {
                "scene_index": 2,
                "start_second": 4,
                "end_second": 8,
                "visual": "Show the player retrying with a better move.",
                "subtitle": "Try Now",
            },
        ],
        creative_strategy=creative_strategy,
    )

    assert "Market game style pack: IN India" in prompt
    assert "Game interest hypothesis:" in prompt
    assert "fast challenge and retry loop" in prompt
    assert "AAA-style inspiration:" in prompt
    assert "open-world action adventure" in prompt
    assert "cinematic RPG progression" in prompt
    assert "Gameplay process:" in prompt
    assert "player goal" in prompt
    assert "opening conflict" in prompt
    assert "player actions" in prompt
    assert "progression feedback" in prompt
    assert "ending transition" in prompt
    assert "culture-inspired epic fantasy" in prompt
    assert "Cultural safety:" in prompt
    assert "real deity names or real religious figures" in prompt
    assert "scripture, mantras, sacred text, or religious claims" in prompt
    strategy_section = prompt.split("Market game style pack:", 1)[1]
    for banned in ("Ganesha", "Shiva", "Krishna", "casino", "slot", "jackpot", "cash"):
        assert banned not in strategy_section


@pytest.mark.asyncio
async def test_openai_video_storyboard_payload_compacts_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = OpenAILLMProvider(api_key="test-key", model="test-model")
    captured: dict[str, object] = {}
    strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "ecommerce",
        "video_guidance": {"opening": "Lead with the routine problem."},
        "raw_content": "STRATEGY RAW SHOULD NOT LEAK",
    }

    async def fake_json_completion(system: str, user: str) -> dict:
        captured["payload"] = json.loads(user)
        return {
            "duration_seconds": 6,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 6,
                    "visual": "Show product in use.",
                    "subtitle": "Shop Now",
                    "motion": "Simple push in.",
                    "voiceover": None,
                    "source_asset_ids": ["asset-1"],
                    "notes": "Safe scene.",
                }
            ],
            "rationale": "Short v2 test.",
        }

    monkeypatch.setattr(provider, "_json_completion", fake_json_completion)
    campaign = Campaign(
        id="campaign-1",
        name="Glow Serum",
        product_name="Glow Serum",
        audience_description="Female 25-34",
        metadata_json={
            "creative_strategy": strategy,
            "raw_content": "CAMPAIGN RAW SHOULD NOT LEAK",
            "landing_page": {"title": "Glow Serum", "text_excerpt": "landing copy " * 300},
        },
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="A quick skincare routine.",
        metadata_json={
            "creative_strategy": strategy,
            "raw_content": "DRAFT RAW SHOULD NOT LEAK",
        },
    )
    asset = CreativeAsset(
        id="asset-1",
        campaign_id="campaign-1",
        draft_id=draft.id,
        prompt="Show serum on a bathroom counter.",
        metadata_json={
            "creative_strategy": strategy,
            "raw_content": "ASSET RAW SHOULD NOT LEAK",
            "image_index": 1,
        },
    )

    await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=[asset],
        duration_seconds=6,
        aspect_ratio="9:16",
        context={"creative_strategy": strategy},
        instructions=None,
    )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    payload_text = json.dumps(payload, ensure_ascii=False)
    assert "raw_content" not in payload_text
    assert "SHOULD NOT LEAK" not in payload_text
    assert payload["campaign"]["metadata"]["creative_strategy"]["schema_version"] == (
        "creative_strategy.v2"
    )
    assert len(payload["campaign"]["metadata"]["landing_page"]["text_excerpt"]) <= 600
    assert payload["copy_draft"]["metadata"]["creative_strategy"]["schema_version"] == (
        "creative_strategy.v2"
    )
    assert payload["assets"][0]["metadata"]["image_index"] == 1
    assert payload["assets"][0]["metadata"]["creative_strategy"]["schema_version"] == (
        "creative_strategy.v2"
    )


def test_video_storyboard_prompt_includes_country_concepts_and_text_layout_rules() -> None:
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "country": "\u5370\u5ea6",
            "event_name": "\u9996\u5145",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 3,
                "visual": "Open with a strong epic GAJA hook.",
                "subtitle": "Start",
            },
            {
                "scene_index": 2,
                "start_second": 3,
                "end_second": 12,
                "visual": "Move through the game world.",
                "subtitle": "Play Now",
            },
        ],
        creative_strategy=creative_strategy,
    )

    assert "Country style pack: IN India" not in prompt
    assert "original Indian epic guardian" not in prompt
    assert "Variant visual concepts:" not in prompt
    assert "india_epic_guardian" not in prompt
    assert "india_royal_portal" not in prompt
    assert "india_mythic_neon_lobby" not in prompt
    assert "Text layout rules:" not in prompt
    assert "premium neon" not in prompt.casefold()


def test_video_storyboard_prompt_includes_landing_visual_reference() -> None:
    creative_strategy = {
        "template_id": "custom_reference",
        "landing_visual_reference": {
            "source": "reference_image",
            "status": "analyzed",
            "palette": ["near-black navy background"],
            "surface_style": ["brushed metal app surface"],
            "gameplay_moment_archetypes": ["failed attempt and retry moment"],
            "composition_cues": ["visible challenge setup with reward cue"],
            "video_recipe": {
                "duration_seconds": 12,
                "beats": [
                    "0-2s: metallic GAJA hook with visible challenge setup",
                    "10-12s: simple Start / Play Now CTA beat",
                ],
            },
        },
        "negative_style_cues": ["childlike puzzle blocks"],
    }

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 2,
                "visual": "Open on GAJA777.",
            }
        ],
        creative_strategy=creative_strategy,
    )

    assert "Landing visual reference" in prompt
    assert "brushed metal app surface" in prompt
    assert "near-black navy background" in prompt
    assert "failed attempt and retry moment" in prompt
    assert "visible challenge setup with reward cue" in prompt
    assert "0-2s: metallic GAJA hook with visible challenge setup" in prompt
    assert "10-12s: simple Start / Play Now CTA beat" in prompt
    assert "Avoid style cues: childlike puzzle blocks" in prompt
    prompt_text = prompt.casefold()
    assert "premium cards" not in prompt_text
    assert "card carousel" not in prompt_text
    assert "end card" not in prompt_text


def test_video_storyboard_prompt_sanitizes_landing_visual_reference_lists() -> None:
    creative_strategy = {
        "template_id": "custom_reference",
        "first_frame": {
            "role": "hook",
            "visual_must_include": ["dark neon GAJA777 lobby"],
        },
        "last_frame": {
            "role": "cta",
            "visual_must_include": ["GAJA777 premium game lobby"],
        },
        "landing_visual_reference": {
            "palette": [
                "casino gold glow",
                "cash-green accent",
            ],
            "surface_style": [
                "slot-machine chrome framing",
                "metallic title treatment",
            ],
            "composition_cues": ["dark premium mobile game lobby"],
        },
    }

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 2,
                "visual": "Open on GAJA777.",
            }
        ],
        creative_strategy=creative_strategy,
    )

    strategy_section = prompt.split("creative_strategy:", 1)[1].lower()
    assert "casino" not in strategy_section
    assert "slot" not in strategy_section
    assert "cash" not in strategy_section
    assert "treatment" not in strategy_section
    assert "styling" in strategy_section


def test_video_storyboard_prompt_sanitizes_strategy_list_fields() -> None:
    creative_strategy = {
        "template_id": "custom_reference",
        "first_frame": {
            "role": "hook",
            "visual_must_include": ["dark neon GAJA777 lobby"],
        },
        "last_frame": {
            "role": "cta",
            "visual_must_include": ["GAJA777 premium game lobby"],
        },
        "video_recipe": {
            "beats": [
                "0-2s: metallic title treatment over the GAJA777 lobby",
                "10-12s: show the end card treatment clearly",
            ]
        },
        "negative_style_cues": [
            "metallic title treatment overlays",
            "clinical treatment graphics",
        ],
        "motion_direction": [
            "Use a slow title treatment reveal before the card fan-out.",
        ],
        "compliance_guardrails": [
            "Remove any treatment-like medical iconography from the end card.",
        ],
    }

    prompt = _storyboard_to_prompt(
        [
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 2,
                "visual": "Open on GAJA777.",
            }
        ],
        creative_strategy=creative_strategy,
    )

    assert "title treatment" not in prompt
    assert "clinical treatment" not in prompt
    assert "treatment-like" not in prompt
    assert "title styling" in prompt
    assert "clinical styling" in prompt


@pytest.mark.parametrize("revision", [False, True])
def test_video_storyboard_text_prompt_requires_selected_source_image_ids(
    revision: bool,
) -> None:
    prompt = _video_storyboard_text_system_prompt(revision=revision)

    assert "If assets is non-empty" in prompt
    assert "every scene block must include `Source image id notes:`" in prompt
    assert "one or more exact ids from `selected_asset_ids`" in prompt
    assert "Never write `No source image provided` when assets is non-empty" in prompt
    assert "Selected images are optional for script generation" in prompt
    assert "Use selected images only when assets are provided" in prompt
    assert (
        "If assets is empty, write `Source image id notes: No reference image required.`"
        in prompt
    )


@pytest.mark.asyncio
async def test_mock_provider_revises_video_storyboard() -> None:
    provider = MockLLMProvider()
    campaign = Campaign(
        id="campaign-1",
        name="India TV campaign",
        product_name="India TV",
        audience_description="Men 25-45",
        metadata_json={},
    )
    asset = CreativeAsset(
        id="asset-1",
        campaign_id="campaign-1",
        draft_id="draft-1",
        prompt="Show live TV channels on a phone.",
        size="1:1",
        metadata_json={},
    )

    storyboard = await provider.revise_video_storyboard(
        campaign=campaign,
        draft=None,
        assets=[asset],
        duration_seconds=12,
        aspect_ratio="9:16",
        context={"landing_page": {"title": "Free Live TV"}},
        current_storyboard=[
            {
                "scene_index": 1,
                "start_second": 0,
                "end_second": 4,
                "visual": "Open with the app benefit.",
                "source_asset_ids": ["asset-1"],
            }
        ],
        current_storyboard_text="Scene 1: Open with the app benefit.",
        feedback="Make the hook faster.",
    )

    assert storyboard.duration_seconds == 12
    assert storyboard.aspect_ratio == "9:16"
    assert storyboard.scenes[0].source_asset_ids == ["asset-1"]
    assert storyboard.scenes[0].notes == "Revision applied: Make the hook faster."


@pytest.mark.asyncio
async def test_mock_provider_v2_storyboard_respects_short_duration() -> None:
    provider = MockLLMProvider()
    strategy = {
        "schema_version": "creative_strategy.v2",
        "vertical": "ecommerce",
        "video_guidance": {
            "duration_adaptive": True,
            "short_video_rules": ["Pain point, product, CTA."],
        },
    }
    campaign = Campaign(
        id="campaign-1",
        name="Glow Serum",
        product_name="Glow Serum",
        audience_description="Female 25-34",
        metadata_json={"creative_strategy": strategy},
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="A quick skincare routine.",
        metadata_json={"creative_strategy": strategy},
    )

    storyboard = await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=[],
        duration_seconds=6,
        aspect_ratio="9:16",
        context={"creative_strategy": strategy},
        instructions=None,
    )

    assert storyboard.duration_seconds == 6
    assert storyboard.scenes[-1].end_second == 6
    assert len(storyboard.scenes) <= 3


@pytest.mark.asyncio
async def test_mock_provider_v2_game_storyboard_uses_market_gameplay_process() -> None:
    provider = MockLLMProvider()
    strategy = build_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
            "country": "India",
            "work_order": {
                "parsed_fields": {
                    "gender": "Male",
                    "age_min": 18,
                    "age_max": 24,
                }
            },
            "brief": "Create a cinematic game ad with a playable challenge process.",
        }
    )
    campaign = Campaign(
        id="campaign-1",
        name="GAJA campaign",
        product_name="GAJA777",
        audience_description="India male 18-24",
        metadata_json={"creative_strategy": strategy},
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Create a short game challenge ad.",
        metadata_json={"creative_strategy": strategy},
    )

    storyboard = await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=[],
        duration_seconds=12,
        aspect_ratio="9:16",
        context={"creative_strategy": strategy},
        instructions=None,
    )

    combined = " ".join(scene.visual for scene in storyboard.scenes)
    assert "open-world action adventure" in combined
    assert "fast challenge and retry loop" in combined
    assert "player action:" in combined
    assert "progression feedback:" in combined
    assert "ending transition:" in combined
    for banned in ("Ganesha", "Shiva", "Krishna", "casino", "slot", "jackpot", "cash"):
        assert banned.lower() not in combined.lower()


@pytest.mark.asyncio
async def test_mock_provider_uses_premium_gaja_brand_storyboard() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )
    campaign = Campaign(
        id="campaign-1",
        name="GAJA777 campaign",
        product_name="GAJA777",
        audience_description="India users",
        metadata_json={"creative_strategy": creative_strategy},
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="GAJA777 ad copy.",
        primary_text="Explore GAJA777 game lobby.",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    storyboard = await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=[],
        duration_seconds=12,
        aspect_ratio="9:16",
        context={"creative_strategy": creative_strategy},
        instructions=None,
    )

    assert "dark neon app lobby" not in storyboard.scenes[0].visual
    assert "metallic GAJA wordmark" not in storyboard.scenes[0].visual
    assert "no visible brand-number text" not in storyboard.scenes[0].visual
    assert "no visible numeric suffix" not in storyboard.scenes[0].visual
    assert "title treatment" not in storyboard.scenes[0].visual
    assert "premium neon game lobby" not in storyboard.scenes[-1].visual
    assert storyboard.scenes[-1].subtitle in {"Start", "Play Now"}


@pytest.mark.asyncio
async def test_mock_provider_uses_country_epic_gaja_storyboard() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "country": "\u5370\u5ea6",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )
    campaign = Campaign(
        id="campaign-1",
        name="GAJA campaign",
        product_name="GAJA777",
        audience_description="India users",
        metadata_json={"creative_strategy": creative_strategy},
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Create a safe epic GAJA game-world video.",
        primary_text="Explore GAJA game worlds.",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    storyboard = await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=[],
        duration_seconds=12,
        aspect_ratio="9:16",
        context={"creative_strategy": creative_strategy},
        instructions=None,
    )

    assert "original Indian epic guardian" not in storyboard.scenes[0].visual
    assert "mandala light geometry" not in storyboard.scenes[0].visual
    assert "metallic GAJA" not in storyboard.scenes[0].visual
    assert "no visible brand-number text" not in storyboard.scenes[0].visual
    assert "premium neon game lobby" not in storyboard.scenes[-1].visual
    combined = " ".join(scene.visual for scene in storyboard.scenes)
    for banned in ("777", "Luck", "casino", "slot", "jackpot", "cash", "coin", "recharge"):
        assert banned.lower() not in combined.lower()


@pytest.mark.asyncio
async def test_mock_provider_gaja_video_avoids_banned_text_and_props() -> None:
    provider = MockLLMProvider()
    creative_strategy = build_game_creative_strategy(
        {
            "product_name": "GAJA777",
            "landing_url": "https://www.gaja777.game/#/?invite=YBG71118&register=true",
        }
    )
    campaign = Campaign(
        id="campaign-1",
        name="GAJA777 campaign",
        product_name="GAJA777",
        audience_description="India users",
        metadata_json={"creative_strategy": creative_strategy},
    )
    draft = CopyDraft(
        id="draft-1",
        campaign_id="campaign-1",
        topic_id="topic-1",
        body="Create a safe game-lobby video.",
        primary_text="Explore the app lobby.",
        version=1,
        metadata_json={"creative_strategy": creative_strategy},
    )

    storyboard = await provider.generate_video_storyboard(
        campaign=campaign,
        draft=draft,
        assets=[],
        duration_seconds=12,
        aspect_ratio="9:16",
        context={"creative_strategy": creative_strategy},
        instructions=None,
    )

    combined = " ".join(
        " ".join(
            item
            for item in (
                scene.visual,
                scene.subtitle or "",
                scene.voiceover or "",
                scene.notes or "",
            )
            if item
        )
        for scene in storyboard.scenes
    )
    assert "metallic GAJA" not in combined
    assert "no visible brand-number text" not in combined
    assert "dark neon" not in combined.lower()
    assert "premium neon" not in combined.lower()
    for banned in (
        "777",
        "Luck",
        "\u8d62\u94b1",
        "\u63d0\u73b0",
        "\u91d1\u5e01\u96e8",
        "\u8d4c\u573a\u684c\u9762",
        "casino",
        "cash",
        "coin",
        "withdraw",
    ):
        assert banned.lower() not in combined.lower()


@pytest.mark.asyncio
async def test_mock_provider_streams_video_storyboard_revision_text() -> None:
    provider = MockLLMProvider()
    campaign = Campaign(
        id="campaign-1",
        name="India TV campaign",
        product_name="India TV",
        audience_description="Men 25-45",
        metadata_json={},
    )
    asset = CreativeAsset(
        id="asset-1",
        campaign_id="campaign-1",
        draft_id="draft-1",
        prompt="Show live TV channels on a phone.",
        size="1:1",
        metadata_json={},
    )

    chunks = [
        chunk
        async for chunk in provider.stream_video_storyboard_revision_text(
            campaign=campaign,
            draft=None,
            assets=[asset],
            duration_seconds=12,
            aspect_ratio="9:16",
            context={"landing_page": {"title": "Free Live TV"}},
            current_storyboard=[
                {
                    "scene_index": 1,
                    "visual": "Open with the app benefit.",
                    "source_asset_ids": ["asset-1"],
                }
            ],
            current_storyboard_text="Scene 1: Open with the app benefit.",
            feedback="Make the hook faster.",
        )
    ]

    text = "".join(chunks)
    assert len(chunks) > 1
    assert "Revision applied: Make the hook faster." in text
    assert "asset-1" in text


def test_video_storyboard_rewrite_request_accepts_current_script() -> None:
    payload = VideoStoryboardRewriteRequest(
        campaign_id="campaign-1",
        creative_asset_ids=["asset-1"],
        draft_id="draft-1",
        duration_seconds=12,
        aspect_ratio="9:16",
        storyboard=[{"scene_index": 1, "visual": "Open with the app benefit."}],
        storyboard_text="Scene 1: Open with the app benefit.",
        feedback="Shorten subtitles.",
    )

    assert payload.feedback == "Shorten subtitles."
    assert payload.storyboard_text == "Scene 1: Open with the app benefit."


def test_video_source_image_prefers_local_data_url_for_local_storage(tmp_path) -> None:
    image_path = tmp_path / "images" / "campaign-1" / "local.jpeg"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image-bytes")
    image_storage = ImageStorageService(
        Settings(
            local_storage_root=str(tmp_path),
            object_storage_provider="local",
            public_base_url="http://127.0.0.1:8001",
        )
    )
    asset = CreativeAsset(
        id="asset-1",
        campaign_id="campaign-1",
        url="http://127.0.0.1:8001/storage/images/local.jpeg",
        storage_key="local://images/campaign-1/local.jpeg",
        metadata_json={"provider_image_url": "https://example.com/provider.jpeg?signature=1"},
    )

    url = _resolve_video_source_image_url(asset, image_storage)

    assert url == "data:image/jpeg;base64,aW1hZ2UtYnl0ZXM="


def test_video_source_image_falls_back_to_provider_url_when_local_file_missing() -> None:
    image_storage = ImageStorageService(
        Settings(
            object_storage_provider="local",
            public_base_url="http://127.0.0.1:8001",
        )
    )
    asset = CreativeAsset(
        id="asset-1",
        campaign_id="campaign-1",
        url="http://127.0.0.1:8001/storage/images/local.jpeg",
        storage_key="local://images/local.jpeg",
        metadata_json={"provider_image_url": "https://example.com/provider.jpeg?signature=1"},
    )

    url = _resolve_video_source_image_url(asset, image_storage)

    assert url == "https://example.com/provider.jpeg?signature=1"


def test_provider_request_payload_redacts_inline_image_data_urls() -> None:
    payload = {
        "model": "seedance",
        "content": [
            {"type": "text", "text": "Create a video."},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/jpeg;base64,aW1hZ2UtYnl0ZXM="},
            },
        ],
    }

    redacted = _redact_provider_request_payload(payload)

    assert redacted["content"][1]["image_url"]["url"] == (
        "data:image/jpeg;base64,<redacted 11 bytes>"
    )
    assert payload["content"][1]["image_url"]["url"].endswith("aW1hZ2UtYnl0ZXM=")
