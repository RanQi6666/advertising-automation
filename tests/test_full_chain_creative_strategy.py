import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.schemas.copywriting import CopyGenerateRequest
from backend.app.schemas.creative import CreativeGenerateRequest
from backend.app.schemas.topic import TopicGenerateRequest
from backend.app.schemas.video import VideoGenerateRequest, VideoStoryboardGenerateRequest
from backend.app.services.copywriting_service import CopywritingService
from backend.app.services.creative_service import CreativeService
from backend.app.services.creative_strategy_builder import build_creative_strategy
from backend.app.services.topic_service import TopicService
from backend.app.services.video_service import VideoService


async def _session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@pytest.mark.asyncio
async def test_creative_strategy_v2_propagates_across_generation_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_PROVIDER", "placeholder")
    get_settings.cache_clear()
    engine, session_factory = await _session_factory()
    try:
        async with session_factory() as session:
            strategy = build_creative_strategy(
                {
                    "product_name": "Puzzle Quest",
                    "landing_url": "https://play.example.sg/level-challenge",
                    "country": "Singapore",
                    "audience_description": "Female 25-34",
                    "brief": "Create a level challenge game ad.",
                }
            )
            campaign = Campaign(
                name="Puzzle Quest SG",
                objective="traffic",
                product_name="Puzzle Quest",
                audience_description="Female 25-34",
                metadata_json={
                    "creative_strategy": strategy,
                    "work_order": {
                        "country": "Singapore",
                        "landing_url": "https://play.example.sg/level-challenge",
                        "parsed_fields": {
                            "gender": "Female",
                            "age_min": 25,
                            "age_max": 34,
                        },
                    },
                    "landing_page": {
                        "url": "https://play.example.sg/level-challenge",
                        "status": "provided",
                    },
                },
            )
            session.add(campaign)
            await session.commit()

            topics = await TopicService().generate_topics(
                session,
                TopicGenerateRequest(campaign_id=campaign.id, limit=3),
            )
            assert len(topics) == 3
            assert len({topic.source_data["topic_angle"]["angle_type"] for topic in topics}) == 3
            for topic in topics:
                assert (
                    topic.source_data["creative_strategy"]["schema_version"]
                    == "creative_strategy.v2"
                )
                assert topic.source_data["creative_strategy"] == strategy

            campaign_metadata = dict(campaign.metadata_json or {})
            campaign_metadata.pop("creative_strategy", None)
            campaign.metadata_json = campaign_metadata
            await session.flush()
            await session.commit()
            await session.refresh(campaign)
            assert "creative_strategy" not in (campaign.metadata_json or {})

            draft = await CopywritingService().generate_copy(
                session,
                CopyGenerateRequest(topic_id=topics[0].id),
            )
            assert (
                draft.metadata_json["creative_strategy"]["schema_version"]
                == "creative_strategy.v2"
            )
            assert (
                draft.metadata_json["creative_strategy"]
                == topics[0].source_data["creative_strategy"]
            )

            assets = await CreativeService().generate_creatives(
                session,
                CreativeGenerateRequest(draft_id=draft.id, count=1, size="1:1"),
            )
            assert (
                assets[0].metadata_json["creative_strategy"]["schema_version"]
                == "creative_strategy.v2"
            )
            assert (
                assets[0].metadata_json["creative_strategy"]
                == draft.metadata_json["creative_strategy"]
            )

            storyboard = await VideoService().generate_storyboard(
                session,
                VideoStoryboardGenerateRequest(
                    campaign_id=campaign.id,
                    creative_asset_ids=[assets[0].id],
                    draft_id=draft.id,
                    duration_seconds=6,
                    aspect_ratio="9:16",
                ),
            )
            assert (
                storyboard.metadata_json["creative_strategy"]["schema_version"]
                == "creative_strategy.v2"
            )
            assert (
                storyboard.metadata_json["creative_strategy"]
                == draft.metadata_json["creative_strategy"]
            )
            assert storyboard.duration_seconds == 6
            assert "creative_strategy" not in (campaign.metadata_json or {})

            # Pass storyboard metadata explicitly so this assertion proves storyboard -> video
            # propagation while campaign metadata fallback remains unavailable.
            video = await VideoService().create_video_job(
                session,
                VideoGenerateRequest(
                    campaign_id=campaign.id,
                    creative_asset_ids=[assets[0].id],
                    draft_id=draft.id,
                    prompt=storyboard.prompt,
                    storyboard=storyboard.storyboard,
                    duration_seconds=6,
                    aspect_ratio="9:16",
                    metadata_json=storyboard.metadata_json,
                ),
            )
            assert (
                video.metadata_json["creative_strategy"]["schema_version"]
                == "creative_strategy.v2"
            )
            assert (
                video.metadata_json["creative_strategy"]
                == storyboard.metadata_json["creative_strategy"]
            )
            assert video.duration_seconds == 6
    finally:
        get_settings.cache_clear()
        await engine.dispose()
