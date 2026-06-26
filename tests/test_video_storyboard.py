import asyncio
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import Settings, get_settings
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.schemas.video import (
    VideoGenerateRequest,
    VideoStoryboardGenerateRequest,
    VideoStoryboardRewriteRequest,
)
from backend.app.services import video_service
from backend.app.services.brand_safety_policy import scan_brand_safety
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.video_service import (
    VideoService,
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


def test_video_storyboard_prompt_includes_safe_brand_safety_visual_guidance() -> None:
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

    assert "Brand safety visual guidance" in prompt
    assert scan_brand_safety({"prompt": prompt})["status"] == "passed"
    assert "cash" not in prompt
    assert "bank cards" not in prompt
    assert "discount stickers" not in prompt
    assert "coupons" not in prompt
    assert "casinos" not in prompt
    assert "pills" not in prompt


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
