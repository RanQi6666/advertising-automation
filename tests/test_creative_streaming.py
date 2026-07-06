import asyncio
import logging

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import CreativeStatus
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.ai import GeneratedImage, ImageBrief
from backend.app.schemas.creative import CreativeGenerateRequest
from backend.app.services import creative_service
from backend.app.services.creative_service import CreativeService


class FakeLLMProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_image_briefs(
        self,
        draft: CopyDraft,
        count: int,
        size: str,
        feedback: str | None = None,
        source_asset: CreativeAsset | None = None,
        storyboard_context: dict | None = None,
    ) -> list[ImageBrief]:
        self.calls.append(
            {
                "draft_id": draft.id,
                "count": count,
                "size": size,
                "feedback": feedback,
                "source_asset_id": source_asset.id if source_asset else None,
                "storyboard_context": storyboard_context,
            }
        )
        return [
            ImageBrief(
                image_index=index + 1,
                title=f"Image {index + 1}",
                short_text=f"Text {index + 1}",
                visual_direction=f"Direction {index + 1}",
                size=size,
            )
            for index in range(count)
        ]


class FakeImageProvider:
    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        return [
            GeneratedImage(
                prompt=f"{brief.title}: {brief.visual_direction}",
                storage_key=f"fake://image-{brief.image_index}",
                alt_text=brief.short_text,
                size=brief.size,
                metadata={"provider": "fake", "image_index": brief.image_index},
            )
            for brief in briefs
        ]


class SlowFakeImageProvider(FakeImageProvider):
    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        await asyncio.sleep(0.02)
        return await super().generate_images(briefs)


def _patch_image_provider(monkeypatch, provider):
    captured_settings = []

    def fake_get_image_provider(settings=None):  # noqa: ANN001
        captured_settings.append(settings)
        return provider

    monkeypatch.setattr(creative_service, "get_image_provider", fake_get_image_provider)
    return captured_settings


@pytest.mark.asyncio
async def test_stream_creatives_yields_three_assets_incrementally(monkeypatch) -> None:
    _patch_image_provider(monkeypatch, FakeImageProvider())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(id="campaign-1", name="Campaign", metadata_json={})
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="Ad copy",
            headline="Headline",
            metadata_json={},
        )
        session.add_all([campaign, draft])
        await session.commit()

        service = CreativeService()
        service.llm = FakeLLMProvider()  # type: ignore[assignment]

        events = [
            event
            async for event in service.stream_creatives(
                session,
                CreativeGenerateRequest(draft_id=draft.id, count=3, size="1:1"),
            )
        ]

        asset_events = [event for event in events if event["type"] == "asset"]
        assert events[0] == {"type": "start", "limit": 3, "indices": [1, 2, 3]}
        assert [event["index"] for event in events[1:4]] == [1, 2, 3]
        assert len(asset_events) == 3
        assert {event["index"] for event in asset_events} == {1, 2, 3}
        assert events[-1] == {"type": "done", "generated": 3}

        stored_assets = list((await session.execute(select(CreativeAsset))).scalars().all())
        assert len(stored_assets) == 3
        assert {asset.metadata_json["image_index"] for asset in stored_assets} == {1, 2, 3}
        assert all(asset.metadata_json["streamed"] is True for asset in stored_assets)

    await engine.dispose()


@pytest.mark.asyncio
async def test_generate_creatives_passes_selected_topic_context_to_image_briefs(
    monkeypatch,
) -> None:
    _patch_image_provider(monkeypatch, FakeImageProvider())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    strategy = {
        "schema_version": "creative_strategy.v2",
        "topic_angle_plan": [
            {"slot": 1, "angle_type": "challenge_failure", "purpose": "failure hook"},
            {"slot": 2, "angle_type": "comeback_growth", "purpose": "growth hook"},
            {"slot": 3, "angle_type": "reward_burst", "purpose": "reward hook"},
        ],
        "image_guidance": {"composition": "Use a gameplay visual."},
    }
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(id="campaign-1", name="Campaign", metadata_json={})
        topic = ContentTopic(
            id="topic-1",
            campaign_id=campaign.id,
            title="Reward payoff",
            angle="reward_burst: show the satisfying unlock payoff",
            audience="Mobile game players",
            selling_points=["Unlock payoff", "Fast progress"],
            source_data={
                "creative_strategy": strategy,
                "topic_angle": strategy["topic_angle_plan"][2],
                "angle_type": "reward_burst",
            },
        )
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id=topic.id,
            body="Ad copy",
            headline="Unlock the reward",
            metadata_json={"creative_strategy": strategy},
        )
        session.add_all([campaign, topic, draft])
        await session.commit()

        service = CreativeService()
        service.llm = FakeLLMProvider()  # type: ignore[assignment]

        assets = await service.generate_creatives(
            session,
            CreativeGenerateRequest(draft_id=draft.id, count=3, size="1:1"),
        )

        llm_call = service.llm.calls[0]  # type: ignore[attr-defined]
        selected_topic = llm_call["storyboard_context"]["selected_topic"]
        assert selected_topic["id"] == topic.id
        assert selected_topic["angle_type"] == "reward_burst"
        assert selected_topic["topic_angle"]["purpose"] == "reward hook"
        assert selected_topic["selling_points"] == ["Unlock payoff", "Fast progress"]
        assert all(
            asset.metadata_json["storyboard_context"]["selected_topic"]["angle_type"]
            == "reward_burst"
            for asset in assets
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_stream_creatives_sends_heartbeat_while_images_are_pending(monkeypatch) -> None:
    monkeypatch.setattr(creative_service, "STREAM_HEARTBEAT_SECONDS", 0.001)
    _patch_image_provider(monkeypatch, SlowFakeImageProvider())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(id="campaign-1", name="Campaign", metadata_json={})
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="Ad copy",
            headline="Headline",
            metadata_json={},
        )
        session.add_all([campaign, draft])
        await session.commit()

        service = CreativeService()
        service.llm = FakeLLMProvider()  # type: ignore[assignment]

        events = [
            event
            async for event in service.stream_creatives(
                session,
                CreativeGenerateRequest(draft_id=draft.id, count=1, size="1:1"),
            )
        ]

        heartbeats = [event for event in events if event["type"] == "heartbeat"]
        assert heartbeats
        assert heartbeats[0]["stage"] == "image_generation"
        assert heartbeats[0]["pending_indices"] == [1]
        assert len([event for event in events if event["type"] == "asset"]) == 1
        assert events[-1] == {"type": "done", "generated": 1}

    await engine.dispose()


@pytest.mark.asyncio
async def test_stream_creatives_passes_storyboard_context_and_selected_image_model(
    monkeypatch,
) -> None:
    captured_settings = _patch_image_provider(monkeypatch, FakeImageProvider())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(id="campaign-1", name="Campaign", metadata_json={})
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="Ad copy",
            headline="Headline",
            metadata_json={},
        )
        session.add_all([campaign, draft])
        await session.commit()

        fake_llm = FakeLLMProvider()
        service = CreativeService()
        service.llm = fake_llm  # type: ignore[assignment]
        service.settings = service.settings.model_copy(
            update={
                "image_provider": "gateway",
                "model_gateway_image_model": "default-image",
            }
        )

        events = [
            event
            async for event in service.stream_creatives(
                session,
                CreativeGenerateRequest(
                    draft_id=draft.id,
                    count=1,
                    size="1:1",
                    model_id="custom-image-model",
                    storyboard_text="Scene 1: open with the app in a bright home scene.",
                ),
            )
        ]

        asset_event = next(event for event in events if event["type"] == "asset")
        assert fake_llm.calls[-1]["storyboard_context"]["storyboard_text"].startswith(
            "Scene 1"
        )
        assert captured_settings[-1].model_gateway_image_model == "custom-image-model"
        assert asset_event["asset"]["metadata_json"]["storyboard_context"][
            "storyboard_text"
        ].startswith("Scene 1")
        assert asset_event["asset"]["metadata_json"]["image_model"] == "custom-image-model"
        assert asset_event["asset"]["metadata_json"]["image_provider"] == "gateway"

    await engine.dispose()


@pytest.mark.asyncio
async def test_stream_creatives_marks_video_keyframe_variant_groups(monkeypatch) -> None:
    _patch_image_provider(monkeypatch, FakeImageProvider())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(id="campaign-1", name="Campaign", metadata_json={})
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="Ad copy",
            headline="Headline",
            metadata_json={},
        )
        session.add_all([campaign, draft])
        await session.commit()

        fake_llm = FakeLLMProvider()
        service = CreativeService()
        service.llm = fake_llm  # type: ignore[assignment]

        events = [
            event
            async for event in service.stream_creatives(
                session,
                CreativeGenerateRequest(
                    draft_id=draft.id,
                    count=6,
                    size="9:16",
                    generation_mode="video_keyframe_variants",
                    variant_count=3,
                    frames_per_variant=2,
                    video_duration_seconds=12,
                    storyboard_text="12 second script: hook, benefit, final action.",
                ),
            )
        ]

        asset_events = [event for event in events if event["type"] == "asset"]
        assert events[0] == {"type": "start", "limit": 6, "indices": [1, 2, 3, 4, 5, 6]}
        assert len(asset_events) == 6
        assert fake_llm.calls[-1]["storyboard_context"]["keyframe_plan"] == {
            "mode": "video_keyframe_variants",
            "variant_count": 3,
            "frames_per_variant": 2,
            "video_duration_seconds": 12,
            "total_images": 6,
        }

        metadata_by_index = {
            event["index"]: event["asset"]["metadata_json"] for event in asset_events
        }
        assert [
            (
                metadata_by_index[index]["keyframe_group"],
                metadata_by_index[index]["keyframe_role"],
                metadata_by_index[index]["keyframe_position"],
            )
            for index in range(1, 7)
        ] == [
            (1, "first_frame", 1),
            (1, "last_frame", 2),
            (2, "first_frame", 1),
            (2, "last_frame", 2),
            (3, "first_frame", 1),
            (3, "last_frame", 2),
        ]
        assert all(
            metadata["keyframe_group_size"] == 2
            and metadata["keyframe_variant_count"] == 3
            and metadata["video_duration_seconds"] == 12
            for metadata in metadata_by_index.values()
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_stream_creatives_generates_selected_keyframe_pair(monkeypatch) -> None:
    _patch_image_provider(monkeypatch, FakeImageProvider())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(id="campaign-1", name="Campaign", metadata_json={})
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="Ad copy",
            headline="Headline",
            metadata_json={},
        )
        session.add_all([campaign, draft])
        await session.commit()

        fake_llm = FakeLLMProvider()
        service = CreativeService()
        service.llm = fake_llm  # type: ignore[assignment]

        events = [
            event
            async for event in service.stream_creatives(
                session,
                CreativeGenerateRequest(
                    draft_id=draft.id,
                    count=2,
                    size="9:16",
                    target_indices=[3, 4],
                    generation_mode="video_keyframe_variants",
                    variant_count=3,
                    frames_per_variant=2,
                    video_duration_seconds=12,
                ),
            )
        ]

        asset_events = [event for event in events if event["type"] == "asset"]
        assert events[0] == {"type": "start", "limit": 2, "indices": [3, 4]}
        assert fake_llm.calls[-1]["count"] == 2
        assert [event["index"] for event in asset_events] == [3, 4]

        metadata_by_index = {
            event["index"]: event["asset"]["metadata_json"] for event in asset_events
        }
        assert metadata_by_index[3]["keyframe_group"] == 2
        assert metadata_by_index[3]["keyframe_role"] == "first_frame"
        assert metadata_by_index[4]["keyframe_group"] == 2
        assert metadata_by_index[4]["keyframe_role"] == "last_frame"

    await engine.dispose()


@pytest.mark.asyncio
async def test_stream_creatives_logs_image_stage_timings(monkeypatch, caplog) -> None:
    caplog.set_level(logging.INFO, logger="backend.app.services.image_generation_timing")
    _patch_image_provider(monkeypatch, FakeImageProvider())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(id="campaign-1", name="Campaign", metadata_json={})
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="Ad copy",
            headline="Headline",
            metadata_json={},
        )
        session.add_all([campaign, draft])
        await session.commit()

        service = CreativeService()
        service.llm = FakeLLMProvider()  # type: ignore[assignment]

        events = [
            event
            async for event in service.stream_creatives(
                session,
                CreativeGenerateRequest(draft_id=draft.id, count=1, size="1:1"),
                task_id="task-1",
            )
        ]

        assert events[-1] == {"type": "done", "generated": 1}
        records = [
            record.image_generation
            for record in caplog.records
            if getattr(record, "image_generation", None)
        ]
        stages = {record["stage"] for record in records}
        assert {"image_brief", "provider_request", "db_commit"} <= stages
        assert all(record["task_id"] == "task-1" for record in records)
        assert all(record["duration_ms"] >= 0 for record in records)

    await engine.dispose()


@pytest.mark.asyncio
async def test_regenerate_creative_creates_new_version_with_feedback(monkeypatch) -> None:
    _patch_image_provider(monkeypatch, FakeImageProvider())
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(id="campaign-1", name="Campaign", metadata_json={})
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id="topic-1",
            body="Ad copy",
            headline="Headline",
            metadata_json={},
        )
        source_asset = CreativeAsset(
            id="creative-1",
            campaign_id=campaign.id,
            draft_id=draft.id,
            prompt="Old prompt",
            alt_text="Old image",
            size="1:1",
            version=1,
            status=CreativeStatus.GENERATED.value,
            metadata_json={
                "image_index": 2,
                "generation_mode": "video_keyframe_variants",
                "keyframe_group": 1,
                "keyframe_role": "last_frame",
                "keyframe_position": 2,
                "keyframe_group_size": 2,
                "keyframe_variant_count": 3,
                "video_duration_seconds": 12,
            },
        )
        session.add_all([campaign, draft, source_asset])
        await session.commit()

        fake_llm = FakeLLMProvider()
        service = CreativeService()
        service.llm = fake_llm  # type: ignore[assignment]

        regenerated = await service.regenerate_creative(
            session=session,
            creative_id=source_asset.id,
            feedback="Make the product larger and reduce text.",
        )

        assert regenerated.id != source_asset.id
        assert regenerated.version == 2
        assert regenerated.metadata_json["image_index"] == 2
        assert regenerated.metadata_json["generation_mode"] == "video_keyframe_variants"
        assert regenerated.metadata_json["keyframe_group"] == 1
        assert regenerated.metadata_json["keyframe_role"] == "last_frame"
        assert regenerated.metadata_json["keyframe_position"] == 2
        assert regenerated.metadata_json["keyframe_group_size"] == 2
        assert regenerated.metadata_json["keyframe_variant_count"] == 3
        assert regenerated.metadata_json["video_duration_seconds"] == 12
        assert regenerated.metadata_json["revision_feedback"] == (
            "Make the product larger and reduce text."
        )
        assert regenerated.metadata_json["source_creative_asset_id"] == source_asset.id
        assert fake_llm.calls[-1]["feedback"] == "Make the product larger and reduce text."
        assert fake_llm.calls[-1]["source_asset_id"] == source_asset.id
        await session.refresh(source_asset)
        assert source_asset.status == CreativeStatus.NEEDS_REVISION.value

    await engine.dispose()
