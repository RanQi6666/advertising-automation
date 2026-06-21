import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import CreativeStatus
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
    ) -> list[ImageBrief]:
        self.calls.append(
            {
                "draft_id": draft.id,
                "count": count,
                "size": size,
                "feedback": feedback,
                "source_asset_id": source_asset.id if source_asset else None,
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


@pytest.mark.asyncio
async def test_stream_creatives_yields_three_assets_incrementally() -> None:
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
        service.image_provider = FakeImageProvider()  # type: ignore[assignment]

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
async def test_stream_creatives_sends_heartbeat_while_images_are_pending(monkeypatch) -> None:
    monkeypatch.setattr(creative_service, "STREAM_HEARTBEAT_SECONDS", 0.001)
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
        service.image_provider = SlowFakeImageProvider()  # type: ignore[assignment]

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
async def test_regenerate_creative_creates_new_version_with_feedback() -> None:
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
            metadata_json={"image_index": 2},
        )
        session.add_all([campaign, draft, source_asset])
        await session.commit()

        fake_llm = FakeLLMProvider()
        service = CreativeService()
        service.llm = fake_llm  # type: ignore[assignment]
        service.image_provider = FakeImageProvider()  # type: ignore[assignment]

        regenerated = await service.regenerate_creative(
            session=session,
            creative_id=source_asset.id,
            feedback="Make the product larger and reduce text.",
        )

        assert regenerated.id != source_asset.id
        assert regenerated.version == 2
        assert regenerated.metadata_json["image_index"] == 2
        assert regenerated.metadata_json["revision_feedback"] == (
            "Make the product larger and reduce text."
        )
        assert regenerated.metadata_json["source_creative_asset_id"] == source_asset.id
        assert fake_llm.calls[-1]["feedback"] == "Make the product larger and reduce text."
        assert fake_llm.calls[-1]["source_asset_id"] == source_asset.id
        await session.refresh(source_asset)
        assert source_asset.status == CreativeStatus.NEEDS_REVISION.value

    await engine.dispose()
