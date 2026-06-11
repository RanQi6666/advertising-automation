import pytest

from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.schemas.video import VideoGenerateRequest


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
