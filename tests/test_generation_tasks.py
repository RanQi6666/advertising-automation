from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base, utcnow
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import VideoStatus
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.schemas.ai import GeneratedImage, ImageBrief
from backend.app.services import creative_service, video_service
from backend.app.services import generation_task_service as task_module
from backend.app.services.generation_task_service import (
    CALLBACK_QUEUE_NAME,
    IMAGE_QUEUE_NAME,
    TEXT_QUEUE_NAME,
    VIDEO_QUEUE_NAME,
    GenerationTaskService,
    recover_generation_tasks_on_startup,
)


class FakeImageTaskLLMProvider:
    async def generate_image_briefs(
        self,
        draft: CopyDraft,
        count: int,
        size: str,
        feedback: str | None = None,
        source_asset: CreativeAsset | None = None,
        storyboard_context: dict | None = None,
    ) -> list[ImageBrief]:
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


class PartiallyFailingImageProvider:
    async def generate_images(self, briefs: list[ImageBrief]) -> list[GeneratedImage]:
        brief = briefs[0]
        if brief.image_index == 2:
            raise RuntimeError("provider failed for slot 2")
        return [
            GeneratedImage(
                prompt=f"{brief.title}: {brief.visual_direction}",
                storage_key=f"fake://image-{brief.image_index}",
                alt_text=brief.short_text,
                size=brief.size,
                metadata={"provider": "fake", "image_index": brief.image_index},
            )
        ]


class FakeVideoTaskService:
    async def start_video_generation(self, session, video_id: str) -> VideoAsset:
        video = await session.get(VideoAsset, video_id)
        assert video is not None
        video.status = VideoStatus.GENERATING.value
        video.provider_job_id = "provider-video-job-1"
        video.error_message = None
        video.metadata_json = {
            **(video.metadata_json or {}),
            "video_provider": "fake",
            "provider_status": "queued",
        }
        await session.commit()
        await session.refresh(video)
        return video


@pytest.mark.asyncio
async def test_generation_task_processes_topic_generation(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-queue-1",
            name="Queue Campaign",
            product_name="Puzzle Game",
            audience_description="India casual players",
            metadata_json={"work_order": {"parsed_fields": {"country": "India"}}},
        )
        session.add(campaign)
        await session.commit()

        service = GenerationTaskService()
        task = await service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id=campaign.id,
            campaign_id=campaign.id,
            payload={"campaign_id": campaign.id, "limit": 3, "signals": {}},
        )

    await GenerationTaskService().process_task(task.id)

    async with session_factory() as session:
        stored = await session.get(GenerationTask, task.id)
        topics = list((await session.execute(select(ContentTopic))).scalars().all())

    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.queue_name == TEXT_QUEUE_NAME
    assert stored.task_type == "topic_generate"
    assert stored.attempt_count == 1
    assert stored.result_json is not None
    assert len(stored.result_json["topics"]) == 3
    assert len(topics) == 3

    await engine.dispose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_run_in_text_queue_does_not_require_database_session(monkeypatch) -> None:
    def fail_session_factory():
        raise AssertionError("inline text queue capacity should not open a database session")

    async def operation() -> str:
        return "ok"

    monkeypatch.setattr(task_module, "AsyncSessionLocal", fail_session_factory)

    result = await GenerationTaskService().run_in_text_queue(operation)

    assert result == "ok"


@pytest.mark.asyncio
async def test_run_in_video_queue_does_not_require_database_session(monkeypatch) -> None:
    def fail_session_factory():
        raise AssertionError("inline video queue capacity should not open a database session")

    async def operation() -> str:
        return "ok"

    monkeypatch.setattr(task_module, "AsyncSessionLocal", fail_session_factory)

    result = await GenerationTaskService().run_in_video_queue(operation)

    assert result == "ok"


@pytest.mark.asyncio
async def test_generation_task_service_lists_tasks_with_filters_and_summary() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-task-monitor-1",
            name="Task Monitor Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

        service = GenerationTaskService()
        queued_task = await service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id=campaign.id,
            campaign_id=campaign.id,
            payload={"campaign_id": campaign.id},
        )
        failed_image_task = await service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-task-monitor-1",
            campaign_id=campaign.id,
            payload={"draft_id": "draft-task-monitor-1"},
            max_attempts=3,
        )
        running_video_task = await service.create_task(
            session,
            queue_name=VIDEO_QUEUE_NAME,
            task_type="video_generate",
            business_type="video_asset",
            business_id="video-task-monitor-1",
            campaign_id=campaign.id,
            payload={"video_id": "video-task-monitor-1"},
        )
        failed_callback_task = await service.create_task(
            session,
            queue_name=CALLBACK_QUEUE_NAME,
            task_type="ad_generation_callback",
            business_type="ad_generation_job",
            business_id="job-task-monitor-1",
            payload={"job_id": "job-task-monitor-1"},
            max_attempts=1,
        )

        failed_image_task.status = "failed"
        failed_image_task.error_code = "provider_timeout"
        failed_image_task.error_message = "provider timeout"
        failed_image_task.retryable = True
        failed_image_task.attempt_count = 1
        failed_image_task.finished_at = utcnow()
        running_video_task.status = "running"
        running_video_task.attempt_count = 1
        running_video_task.started_at = utcnow()
        failed_callback_task.status = "failed"
        failed_callback_task.error_code = "unknown_provider_error"
        failed_callback_task.error_message = "Callback endpoint returned HTTP 500."
        failed_callback_task.retryable = False
        failed_callback_task.attempt_count = 1
        failed_callback_task.finished_at = utcnow()
        await session.commit()

        listing = await service.list_tasks(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            status="failed",
            limit=20,
        )

    assert [task.id for task in listing.items] == [failed_image_task.id]
    assert listing.total == 1
    assert listing.limit == 20
    assert listing.offset == 0
    assert listing.summary["total"] == 4
    assert listing.summary["by_status"] == {
        "failed": 2,
        "queued": 1,
        "running": 1,
    }
    assert listing.summary["by_queue"][TEXT_QUEUE_NAME] == 1
    assert listing.summary["by_queue"][IMAGE_QUEUE_NAME] == 1
    assert listing.summary["by_queue"][VIDEO_QUEUE_NAME] == 1
    assert listing.summary["by_queue"][CALLBACK_QUEUE_NAME] == 1
    assert listing.summary["retryable_failed_count"] == 1
    assert listing.summary["active_count"] == 2
    assert queued_task.status == "queued"

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_startup_recovery_reschedules_queued_and_marks_running_interrupted(
    monkeypatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-startup-recovery-1",
            name="Startup Recovery Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

        queued_task = await GenerationTaskService().create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id=campaign.id,
            campaign_id=campaign.id,
            payload={"campaign_id": campaign.id, "limit": 3, "signals": {}},
        )
        running_task = await GenerationTaskService().create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-startup-recovery-1",
            campaign_id=campaign.id,
            payload={"draft_id": "draft-startup-recovery-1", "count": 2, "size": "1:1"},
            max_attempts=3,
        )
        running_task.status = "running"
        running_task.attempt_count = 1
        running_task.started_at = utcnow() - timedelta(minutes=5)
        running_task.error_code = None
        running_task.error_message = None
        await session.commit()

    scheduled_task_ids: list[str] = []
    recovery = await recover_generation_tasks_on_startup(schedule_task=scheduled_task_ids.append)

    async with session_factory() as session:
        stored_queued = await session.get(GenerationTask, queued_task.id)
        stored_running = await session.get(GenerationTask, running_task.id)
        summary = await GenerationTaskService().task_summary(session)

    assert recovery.rescheduled_task_ids == [queued_task.id]
    assert recovery.interrupted_task_ids == [running_task.id]
    assert scheduled_task_ids == [queued_task.id]
    assert stored_queued is not None
    assert stored_queued.status == "queued"
    assert stored_running is not None
    assert stored_running.status == "failed"
    assert stored_running.error_code == "task_interrupted"
    assert stored_running.retryable is True
    assert "interrupted" in (stored_running.error_message or "").lower()
    assert summary["resumable_queued_count"] == 1
    assert summary["interrupted_failed_count"] == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_processes_image_generation_with_partial_success(
    monkeypatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        creative_service,
        "get_llm_provider",
        lambda settings: FakeImageTaskLLMProvider(),
    )
    monkeypatch.setattr(
        creative_service,
        "get_image_provider",
        lambda settings: PartiallyFailingImageProvider(),
    )

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-image-queue-1",
            name="Image Queue Campaign",
            metadata_json={},
        )
        topic = ContentTopic(
            id="topic-image-queue-1",
            campaign_id=campaign.id,
            title="Queue topic",
            angle="Queue angle",
            source_data={},
        )
        draft = CopyDraft(
            id="draft-image-queue-1",
            campaign_id=campaign.id,
            topic_id=topic.id,
            body="Ad copy",
            headline="Headline",
            metadata_json={},
        )
        session.add_all([campaign, topic, draft])
        await session.commit()

        task = await GenerationTaskService().create_task(
            session,
            queue_name="image_queue",
            task_type="image_generate",
            business_type="copy_draft",
            business_id=draft.id,
            campaign_id=campaign.id,
            payload={"draft_id": draft.id, "count": 3, "size": "1:1"},
        )

    await GenerationTaskService().process_task(task.id)

    async with session_factory() as session:
        stored = await session.get(GenerationTask, task.id)
        assets = list((await session.execute(select(CreativeAsset))).scalars().all())

    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.queue_name == "image_queue"
    assert stored.task_type == "image_generate"
    assert stored.result_json is not None
    assert stored.result_json["total_count"] == 3
    assert stored.result_json["generated_count"] == 2
    assert stored.result_json["failed_count"] == 1
    assert [(slot["index"], slot["status"]) for slot in stored.result_json["slots"]] == [
        (1, "done"),
        (2, "error"),
        (3, "done"),
    ]
    assert stored.result_json["slots"][1]["message"] == "provider failed for slot 2"
    assert len(stored.result_json["assets"]) == 2
    assert {asset.metadata_json["image_index"] for asset in assets} == {1, 3}

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_processes_video_generation(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(video_service, "VideoService", FakeVideoTaskService)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-video-queue-1",
            name="Video Queue Campaign",
            metadata_json={},
        )
        video = VideoAsset(
            id="video-queue-1",
            campaign_id=campaign.id,
            source_asset_ids=["creative-1", "creative-2"],
            prompt="Create a short ad video",
            storyboard=[],
            duration_seconds=12,
            aspect_ratio="9:16",
            status=VideoStatus.REQUESTED.value,
            metadata_json={},
        )
        session.add_all([campaign, video])
        await session.commit()

        task = await GenerationTaskService().create_task(
            session,
            queue_name=VIDEO_QUEUE_NAME,
            task_type="video_generate",
            business_type="video_asset",
            business_id=video.id,
            campaign_id=campaign.id,
            payload={"video_id": video.id},
        )

    await GenerationTaskService().process_task(task.id)

    async with session_factory() as session:
        stored = await session.get(GenerationTask, task.id)
        stored_video = await session.get(VideoAsset, "video-queue-1")

    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.queue_name == VIDEO_QUEUE_NAME
    assert stored.task_type == "video_generate"
    assert stored.result_json is not None
    assert stored.result_json["video_id"] == "video-queue-1"
    assert stored.result_json["status"] == VideoStatus.GENERATING.value
    assert stored.result_json["provider_job_id"] == "provider-video-job-1"
    assert stored.result_json["video"]["id"] == "video-queue-1"
    assert stored_video is not None
    assert stored_video.status == VideoStatus.GENERATING.value
    assert stored_video.provider_job_id == "provider-video-job-1"

    await engine.dispose()
