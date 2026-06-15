import pytest

from backend.app.core.config import Settings
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.schemas.video import VideoGenerateRequest
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.video_service import (
    _redact_provider_request_payload,
    _resolve_video_source_image_url,
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
