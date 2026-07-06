import asyncio
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.api.v1.endpoints import creatives as creatives_endpoint
from backend.app.api.v1.endpoints import generation_tasks as generation_tasks_endpoint
from backend.app.core.config import get_settings
from backend.app.core.errors import ProviderError
from backend.app.db.base import Base, utcnow
from backend.app.db.models.ad_generation_job import AdGenerationJob
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import VideoStatus
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.user import User
from backend.app.db.models.video_asset import VideoAsset
from backend.app.db.models.work_order import WorkOrder
from backend.app.db.session import get_session
from backend.app.main import create_app
from backend.app.schemas.ai import GeneratedImage, ImageBrief
from backend.app.schemas.generation_task import GenerationTaskRead
from backend.app.services import creative_service, video_service
from backend.app.services import generation_task_dispatcher as dispatcher
from backend.app.services import generation_task_service as task_module
from backend.app.services.collaboration import OperatorContext
from backend.app.services.generation_task_service import (
    CALLBACK_QUEUE_NAME,
    IMAGE_QUEUE_NAME,
    TEXT_QUEUE_NAME,
    VIDEO_QUEUE_NAME,
    GenerationTaskService,
    recover_generation_tasks_on_startup,
    should_schedule_generation_task,
)


class FakeStoryboardTaskService:
    async def generate_storyboard(self, session, payload):
        from backend.app.schemas.video import VideoStoryboardRead

        return VideoStoryboardRead(
            campaign_id=payload.campaign_id,
            draft_id=payload.draft_id,
            creative_asset_ids=payload.creative_asset_ids,
            duration_seconds=payload.duration_seconds,
            aspect_ratio=payload.aspect_ratio,
            storyboard=[
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": payload.duration_seconds,
                    "visual": "Open with the app benefit.",
                    "subtitle": "Start now",
                    "motion": "Fast cuts",
                    "voiceover": "Try it today",
                    "notes": "Use approved copy.",
                }
            ],
            prompt="Scene 1: Open with the app benefit.",
            metadata_json={"provider": "fake", "model": payload.model_id or "fake-text"},
        )

    async def rewrite_storyboard(self, session, payload):
        from backend.app.schemas.video import VideoStoryboardRead

        return VideoStoryboardRead(
            campaign_id=payload.campaign_id,
            draft_id=payload.draft_id,
            creative_asset_ids=payload.creative_asset_ids,
            duration_seconds=payload.duration_seconds,
            aspect_ratio=payload.aspect_ratio,
            storyboard=[
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": payload.duration_seconds,
                    "visual": "Open faster with the app benefit.",
                    "subtitle": "Play now",
                    "motion": "Quick zoom",
                    "voiceover": "Try it today",
                    "notes": f"Revision applied: {payload.feedback}",
                }
            ],
            prompt=f"Revision applied: {payload.feedback}",
            metadata_json={"provider": "fake", "revision_feedback": payload.feedback},
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


OPERATOR_A_ID = "00000000-0000-4000-8000-000000000101"
OPERATOR_B_ID = "00000000-0000-4000-8000-000000000102"
ADMIN_ID = "00000000-0000-4000-8000-000000000199"


def _operator_context(operator_id: str, role: str = "operator") -> OperatorContext:
    return OperatorContext(
        id=operator_id,
        email=f"{operator_id}@example.test",
        full_name=None,
        role=role,
    )


def _operator_headers(operator_id: str) -> dict[str, str]:
    return {"X-Operator-Id": operator_id}


async def _seed_generation_task_users(session_factory) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                User(
                    id=OPERATOR_A_ID,
                    email="generation-operator-a@example.test",
                    full_name="Operator A",
                    role="operator",
                    is_active=True,
                ),
                User(
                    id=OPERATOR_B_ID,
                    email="generation-operator-b@example.test",
                    full_name="Operator B",
                    role="operator",
                    is_active=True,
                ),
                User(
                    id=ADMIN_ID,
                    email="generation-admin@example.test",
                    full_name="Admin",
                    role="admin",
                    is_active=True,
                ),
            ]
        )
        await session.commit()


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
async def test_generation_task_processes_video_storyboard_generation(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(video_service, "VideoService", FakeStoryboardTaskService)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-storyboard-task-1",
            name="Storyboard Task Campaign",
            metadata_json={},
        )
        topic = ContentTopic(
            id="topic-storyboard-task-1",
            campaign_id=campaign.id,
            title="Topic",
            angle="Angle",
        )
        draft = CopyDraft(
            id="draft-storyboard-task-1",
            campaign_id=campaign.id,
            topic_id=topic.id,
            body="Approved copy",
            status="approved",
        )
        session.add_all([campaign, topic, draft])
        await session.commit()

        task = await GenerationTaskService().create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="video_storyboard_generate",
            business_type="copy_draft",
            business_id=draft.id,
            campaign_id=campaign.id,
            payload={
                "campaign_id": campaign.id,
                "creative_asset_ids": [],
                "draft_id": draft.id,
                "duration_seconds": 12,
                "aspect_ratio": "9:16",
                "instructions": "Make it energetic.",
                "model_id": "fake-text-model",
            },
            owner_user_id=OPERATOR_A_ID,
        )

    await GenerationTaskService().process_task(task.id)

    async with session_factory() as session:
        stored = await session.get(GenerationTask, task.id)

    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.queue_name == TEXT_QUEUE_NAME
    assert stored.task_type == "video_storyboard_generate"
    assert stored.business_type == "copy_draft"
    assert stored.campaign_id == "campaign-storyboard-task-1"
    assert stored.result_json is not None
    assert stored.result_json["video_storyboard"]["campaign_id"] == "campaign-storyboard-task-1"
    assert stored.result_json["video_storyboard"]["draft_id"] == "draft-storyboard-task-1"
    assert stored.result_json["storyboard_text"].startswith("镜头 1")
    assert stored.result_json["generated_count"] == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_processes_video_storyboard_rewrite(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(video_service, "VideoService", FakeStoryboardTaskService)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-storyboard-rewrite-task-1",
            name="Storyboard Rewrite Campaign",
            metadata_json={},
        )
        topic = ContentTopic(
            id="topic-storyboard-rewrite-task-1",
            campaign_id=campaign.id,
            title="Topic",
            angle="Angle",
        )
        draft = CopyDraft(
            id="draft-storyboard-rewrite-task-1",
            campaign_id=campaign.id,
            topic_id=topic.id,
            body="Approved copy",
            status="approved",
        )
        session.add_all([campaign, topic, draft])
        await session.commit()

        task = await GenerationTaskService().create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="video_storyboard_rewrite",
            business_type="copy_draft",
            business_id=draft.id,
            campaign_id=campaign.id,
            payload={
                "campaign_id": campaign.id,
                "creative_asset_ids": [],
                "draft_id": draft.id,
                "duration_seconds": 12,
                "aspect_ratio": "9:16",
                "storyboard": [{"scene_index": 1, "visual": "Open with the app."}],
                "storyboard_text": "Scene 1: Open with the app.",
                "feedback": "Make the hook faster.",
                "model_id": "fake-text-model",
            },
            owner_user_id=OPERATOR_A_ID,
        )

    await GenerationTaskService().process_task(task.id)

    async with session_factory() as session:
        stored = await session.get(GenerationTask, task.id)

    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.task_type == "video_storyboard_rewrite"
    assert stored.result_json is not None
    assert stored.result_json["video_storyboard"]["metadata_json"]["revision_feedback"] == (
        "Make the hook faster."
    )
    assert "Open faster" in stored.result_json["storyboard_text"]

    await engine.dispose()


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
        callback_job = AdGenerationJob(
            id="job-task-monitor-1",
            status="returned",
            request_payload={},
            result_payload={"metadata_json": {"campaign_id": campaign.id}},
            metadata_json={},
        )
        session.add_all([campaign, callback_job])
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
    assert listing.summary["target_concurrent_users"] == 30
    assert listing.summary["total_active_capacity"] == 17
    assert listing.summary["failure_codes"] == [
        {"code": "provider_timeout", "count": 1},
        {"code": "unknown_provider_error", "count": 1},
    ]
    assert listing.summary["queue_health"][TEXT_QUEUE_NAME]["queued"] == 1
    assert listing.summary["queue_health"][TEXT_QUEUE_NAME]["concurrency"] == 6
    assert listing.summary["queue_health"][IMAGE_QUEUE_NAME]["failed"] == 1
    assert listing.summary["queue_health"][VIDEO_QUEUE_NAME]["running"] == 1
    assert listing.summary["queue_health"][VIDEO_QUEUE_NAME]["risk_level"] == "low"
    assert queued_task.status == "queued"

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_list_enriches_historical_copy_task_work_order_context(
    monkeypatch,
) -> None:
    class FakeRuntimeMonitor:
        async def runtime_summary(self, queue_names, queue_concurrency):
            return {
                "execution_backend": "background_tasks",
                "redis_queues": {"status": "disabled", "queues": {}, "error": None},
                "worker_health": {
                    "status": "disabled",
                    "online_count": 0,
                    "missing_queues": [],
                    "total_active_tasks": 0,
                    "workers": [],
                    "error": None,
                },
            }

    monkeypatch.setattr(task_module, "GenerationRuntimeMonitor", FakeRuntimeMonitor)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        work_order = WorkOrder(
            id="work-order-task-context-1",
            raw_content="Project GAJA777",
            project_name="GAJA777",
            parsed_fields={},
            metadata_json={},
        )
        campaign = Campaign(
            id="campaign-task-context-1",
            name="GAJA777",
            work_order_id=work_order.id,
            metadata_json={},
        )
        topic = ContentTopic(
            id="topic-task-context-1",
            campaign_id=campaign.id,
            title="Launch hook",
            angle="Open the game portal",
            selling_points=[],
            source_data={},
        )
        older_job = AdGenerationJob(
            id="job-task-context-0",
            status="fields_review",
            request_payload={"work_order": {"structured_fields": {"project_name": "Earlier"}}},
            result_payload={"metadata_json": {"campaign_id": "campaign-older-context"}},
            metadata_json={},
            created_at=utcnow() - timedelta(hours=1),
        )
        job = AdGenerationJob(
            id="job-task-context-1",
            status="image_review",
            request_payload={"work_order": {"structured_fields": {"project_name": "GAJA777"}}},
            result_payload={
                "metadata_json": {
                    "campaign_id": campaign.id,
                    "work_order_id": work_order.id,
                }
            },
            metadata_json={},
            created_at=utcnow(),
        )
        session.add_all([work_order, campaign, topic, older_job, job])
        await session.commit()

        historical_task = await GenerationTaskService().create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="copy_generate",
            business_type="topic",
            business_id=topic.id,
            payload={"topic_id": topic.id},
        )

        listing = await GenerationTaskService().list_tasks(session)
        read_task = GenerationTaskRead.from_model(listing.items[0])

    assert listing.items[0].id == historical_task.id
    assert read_task.campaign_id is None
    assert read_task.display_context == {
        "campaign_id": campaign.id,
        "campaign_name": "GAJA777",
        "work_order_id": work_order.id,
        "work_order_title": "GAJA777",
        "ad_generation_job_id": job.id,
        "ad_generation_job_number": "002",
    }

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_service_scopes_list_and_summary_by_operator(
    monkeypatch,
) -> None:
    class FakeRuntimeMonitor:
        async def runtime_summary(self, queue_names, queue_concurrency):
            return {
                "execution_backend": "celery",
                "redis_queues": {
                    "status": "ok",
                    "total_depth": 4,
                    "queues": {
                        queue_name: {
                            "depth": 1,
                            "concurrency": queue_concurrency[queue_name],
                            "backlog": 0,
                            "pressure_ratio": 0.0,
                        }
                        for queue_name in queue_names
                    },
                    "error": None,
                },
                "worker_health": {
                    "status": "ok",
                    "online_count": 2,
                    "missing_queues": [],
                    "total_active_tasks": 3,
                    "workers": [],
                    "error": None,
                },
            }

    monkeypatch.setattr(task_module, "GenerationRuntimeMonitor", FakeRuntimeMonitor)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-owned-task-monitor-1",
            name="Owned Task Monitor Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

        service = GenerationTaskService()
        operator_task = await service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id=f"{campaign.id}-operator-a",
            campaign_id=campaign.id,
            payload={"campaign_id": campaign.id, "owner": "a"},
            owner_user_id=OPERATOR_A_ID,
        )
        other_operator_task = await service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-owned-task-monitor-b",
            campaign_id=campaign.id,
            payload={"draft_id": "draft-owned-task-monitor-b"},
            owner_user_id=OPERATOR_B_ID,
        )
        unowned_task = await service.create_task(
            session,
            queue_name=VIDEO_QUEUE_NAME,
            task_type="video_generate",
            business_type="video_asset",
            business_id="video-owned-task-monitor-unowned",
            campaign_id=campaign.id,
            payload={"video_id": "video-owned-task-monitor-unowned"},
        )
        other_operator_task.status = "failed"
        other_operator_task.error_code = "provider_timeout"
        other_operator_task.error_message = "provider timeout"
        other_operator_task.retryable = True
        other_operator_task.finished_at = utcnow()
        await session.commit()

        operator_listing = await service.list_tasks(
            session,
            operator=_operator_context(OPERATOR_A_ID),
        )
        admin_listing = await service.list_tasks(
            session,
            operator=_operator_context(ADMIN_ID, role="admin"),
        )

    assert {task.id for task in operator_listing.items} == {
        operator_task.id,
        unowned_task.id,
    }
    assert operator_listing.total == 2
    assert operator_listing.summary["total"] == 2
    assert operator_listing.summary["by_queue"] == {
        TEXT_QUEUE_NAME: 1,
        VIDEO_QUEUE_NAME: 1,
    }
    assert operator_listing.summary["retryable_failed_count"] == 0
    assert operator_listing.summary["redis_queues"]["total_depth"] == 4
    assert operator_listing.summary["worker_health"]["online_count"] == 2

    assert {task.id for task in admin_listing.items} == {
        operator_task.id,
        other_operator_task.id,
        unowned_task.id,
    }
    assert admin_listing.total == 3
    assert admin_listing.summary["total"] == 3
    assert admin_listing.summary["retryable_failed_count"] == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_list_prunes_orphaned_business_tasks() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-visible-task-monitor",
            name="Visible Task Monitor Campaign",
            metadata_json={},
        )
        topic = ContentTopic(
            id="topic-visible-task-monitor",
            campaign_id=campaign.id,
            title="Visible topic",
            angle="Visible angle",
        )
        draft = CopyDraft(
            id="draft-visible-task-monitor",
            campaign_id=campaign.id,
            topic_id=topic.id,
            body="Visible copy",
        )
        session.add_all([campaign, topic, draft])
        await session.commit()

        service = GenerationTaskService()
        valid_campaign_task = await service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id=f"{campaign.id}-request",
            campaign_id=campaign.id,
            payload={"campaign_id": campaign.id},
        )
        valid_draft_task = await service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id=draft.id,
            campaign_id=None,
            payload={"draft_id": draft.id},
        )
        orphan_campaign_task = await service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id="campaign-deleted-task-monitor",
            campaign_id="campaign-deleted-task-monitor",
            payload={"campaign_id": "campaign-deleted-task-monitor"},
        )
        orphan_topic_task = await service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="copy_generate",
            business_type="topic",
            business_id="topic-deleted-task-monitor",
            campaign_id=None,
            payload={"topic_id": "topic-deleted-task-monitor"},
        )
        orphan_draft_task = await service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-deleted-task-monitor",
            campaign_id=None,
            payload={"draft_id": "draft-deleted-task-monitor"},
        )
        orphan_callback_task = await service.create_task(
            session,
            queue_name=CALLBACK_QUEUE_NAME,
            task_type="ad_generation_callback",
            business_type="ad_generation_job",
            business_id="job-deleted-task-monitor",
            campaign_id=None,
            payload={"job_id": "job-deleted-task-monitor"},
        )

        listing = await service.list_tasks(session)
        remaining_rows = list(
            (await session.execute(select(GenerationTask))).scalars().all()
        )

    assert {task.id for task in listing.items} == {
        valid_campaign_task.id,
        valid_draft_task.id,
    }
    assert listing.total == 2
    assert listing.summary["total"] == 2
    assert {task.id for task in remaining_rows} == {
        valid_campaign_task.id,
        valid_draft_task.id,
    }
    assert orphan_campaign_task.id not in {task.id for task in remaining_rows}
    assert orphan_topic_task.id not in {task.id for task in remaining_rows}
    assert orphan_draft_task.id not in {task.id for task in remaining_rows}
    assert orphan_callback_task.id not in {task.id for task in remaining_rows}

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_endpoints_require_operator_and_block_cross_operator_access(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "")
    get_settings.cache_clear()

    class FakeRuntimeMonitor:
        async def runtime_summary(self, queue_names, queue_concurrency):
            return {
                "execution_backend": "background",
                "redis_queues": {"status": "disabled", "queues": {}, "error": None},
                "worker_health": {
                    "status": "disabled",
                    "online_count": 0,
                    "missing_queues": [],
                    "total_active_tasks": 0,
                    "workers": [],
                    "error": None,
                },
            }

    monkeypatch.setattr(task_module, "GenerationRuntimeMonitor", FakeRuntimeMonitor)
    monkeypatch.setattr(
        generation_tasks_endpoint,
        "schedule_generation_task",
        lambda task, background_tasks: None,
    )
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'generation-tasks-api.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    await _seed_generation_task_users(session_factory)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-owned-task-api-1",
            name="Owned Task API Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

        service = GenerationTaskService()
        operator_task = await service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id=f"{campaign.id}-operator-a",
            campaign_id=campaign.id,
            payload={"campaign_id": campaign.id, "owner": "a"},
            owner_user_id=OPERATOR_A_ID,
        )
        other_operator_task = await service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-owned-task-api-b",
            campaign_id=campaign.id,
            payload={"draft_id": "draft-owned-task-api-b"},
            owner_user_id=OPERATOR_B_ID,
            max_attempts=3,
        )
        unowned_task = await service.create_task(
            session,
            queue_name=VIDEO_QUEUE_NAME,
            task_type="video_generate",
            business_type="video_asset",
            business_id="video-owned-task-api-unowned",
            campaign_id=campaign.id,
            payload={"video_id": "video-owned-task-api-unowned"},
        )
        other_operator_task.status = "failed"
        other_operator_task.error_code = "provider_timeout"
        other_operator_task.error_message = "provider timeout"
        other_operator_task.retryable = True
        other_operator_task.attempt_count = 1
        other_operator_task.finished_at = utcnow()
        await session.commit()

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            missing_operator_response = client.get("/api/v1/generation-tasks")
            operator_list_response = client.get(
                "/api/v1/generation-tasks",
                headers=_operator_headers(OPERATOR_A_ID),
            )
            admin_list_response = client.get(
                "/api/v1/generation-tasks",
                headers=_operator_headers(ADMIN_ID),
            )
            forbidden_get_response = client.get(
                f"/api/v1/generation-tasks/{other_operator_task.id}",
                headers=_operator_headers(OPERATOR_A_ID),
            )
            admin_get_response = client.get(
                f"/api/v1/generation-tasks/{other_operator_task.id}",
                headers=_operator_headers(ADMIN_ID),
            )
            forbidden_retry_response = client.post(
                f"/api/v1/generation-tasks/{other_operator_task.id}/retry",
                headers=_operator_headers(OPERATOR_A_ID),
            )
            admin_retry_response = client.post(
                f"/api/v1/generation-tasks/{other_operator_task.id}/retry",
                headers=_operator_headers(ADMIN_ID),
            )
    finally:
        app.dependency_overrides.clear()

    assert missing_operator_response.status_code == 401
    assert operator_list_response.status_code == 200
    operator_payload = operator_list_response.json()
    assert {item["id"] for item in operator_payload["items"]} == {
        operator_task.id,
        unowned_task.id,
    }
    assert operator_payload["total"] == 2
    assert operator_payload["summary"]["total"] == 2
    assert operator_payload["summary"]["by_queue"] == {
        TEXT_QUEUE_NAME: 1,
        VIDEO_QUEUE_NAME: 1,
    }

    assert admin_list_response.status_code == 200
    assert {item["id"] for item in admin_list_response.json()["items"]} == {
        operator_task.id,
        other_operator_task.id,
        unowned_task.id,
    }

    assert forbidden_get_response.status_code == 403
    assert admin_get_response.status_code == 200
    assert admin_get_response.json()["owner_user_id"] == OPERATOR_B_ID
    assert forbidden_retry_response.status_code == 403
    assert admin_retry_response.status_code == 200
    assert admin_retry_response.json()["status"] == "queued"

    await engine.dispose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_generation_task_summary_reports_queue_pressure_and_duration_metrics(
    monkeypatch,
) -> None:
    monkeypatch.setenv("TEXT_QUEUE_CONCURRENCY", "2")
    monkeypatch.setenv("IMAGE_QUEUE_CONCURRENCY", "1")
    monkeypatch.setenv("VIDEO_QUEUE_CONCURRENCY", "1")
    monkeypatch.setenv("CALLBACK_QUEUE_CONCURRENCY", "1")
    monkeypatch.setenv("GENERATION_TASK_TARGET_CONCURRENT_USERS", "30")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-task-pressure-1",
            name="Task Pressure Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

        now = utcnow()
        service = GenerationTaskService()
        queued_text_tasks = [
            await service.create_task(
                session,
                queue_name=TEXT_QUEUE_NAME,
                task_type="topic_generate",
                business_type="campaign",
                business_id=f"{campaign.id}-{index}",
                campaign_id=campaign.id,
                payload={"campaign_id": campaign.id, "index": index},
            )
            for index in range(3)
        ]
        running_image_task = await service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-task-pressure-1",
            campaign_id=campaign.id,
            payload={"draft_id": "draft-task-pressure-1"},
        )
        failed_image_task = await service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-task-pressure-2",
            campaign_id=campaign.id,
            payload={"draft_id": "draft-task-pressure-2"},
        )
        succeeded_video_task = await service.create_task(
            session,
            queue_name=VIDEO_QUEUE_NAME,
            task_type="video_generate",
            business_type="video_asset",
            business_id="video-task-pressure-1",
            campaign_id=campaign.id,
            payload={"video_id": "video-task-pressure-1"},
        )

        for index, task in enumerate(queued_text_tasks):
            task.queued_at = now - timedelta(seconds=120 + index * 30)
        running_image_task.status = "running"
        running_image_task.queued_at = now - timedelta(seconds=150)
        running_image_task.started_at = now - timedelta(seconds=90)
        running_image_task.attempt_count = 1
        failed_image_task.status = "failed"
        failed_image_task.error_code = "provider_timeout"
        failed_image_task.error_message = "provider timeout"
        failed_image_task.retryable = True
        failed_image_task.queued_at = now - timedelta(seconds=240)
        failed_image_task.started_at = now - timedelta(seconds=180)
        failed_image_task.finished_at = now - timedelta(seconds=60)
        failed_image_task.duration_ms = 120_000
        succeeded_video_task.status = "succeeded"
        succeeded_video_task.queued_at = now - timedelta(seconds=300)
        succeeded_video_task.started_at = now - timedelta(seconds=240)
        succeeded_video_task.finished_at = now - timedelta(seconds=120)
        succeeded_video_task.duration_ms = 120_000
        await session.commit()

        summary = await service.task_summary(session)

    text_health = summary["queue_health"][TEXT_QUEUE_NAME]
    image_health = summary["queue_health"][IMAGE_QUEUE_NAME]
    video_health = summary["queue_health"][VIDEO_QUEUE_NAME]

    assert summary["target_concurrent_users"] == 30
    assert summary["total_active_capacity"] == 5
    assert text_health["active"] == 3
    assert text_health["concurrency"] == 2
    assert text_health["backlog"] == 1
    assert text_health["risk_level"] == "high"
    assert text_health["avg_wait_ms"] >= 120_000
    assert text_health["max_wait_ms"] >= text_health["avg_wait_ms"]
    assert image_health["active"] == 1
    assert image_health["failed"] == 1
    assert image_health["avg_wait_ms"] == 60_000
    assert image_health["avg_run_ms"] >= 105_000
    assert image_health["max_run_ms"] >= image_health["avg_run_ms"]
    assert video_health["avg_wait_ms"] == 60_000
    assert video_health["avg_run_ms"] == 120_000
    assert summary["failure_codes"] == [{"code": "provider_timeout", "count": 1}]
    assert summary["slowest_queues"][0]["queue_name"] == TEXT_QUEUE_NAME

    await engine.dispose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_generation_task_summary_includes_runtime_monitor_status(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    active_session: AsyncSession | None = None

    class FakeRuntimeMonitor:
        async def runtime_summary(self, queue_names, queue_concurrency):
            assert active_session is not None
            assert active_session.in_transaction() is False
            return {
                "execution_backend": "celery",
                "redis_queues": {
                    "status": "ok",
                    "total_depth": 2,
                    "queues": {
                        "text_queue": {
                            "depth": 2,
                            "concurrency": queue_concurrency["text_queue"],
                            "backlog": 0,
                            "pressure_ratio": 0.33,
                        }
                    },
                    "error": None,
                },
                "worker_health": {
                    "status": "ok",
                    "online_count": 1,
                    "missing_queues": [],
                    "total_active_tasks": 1,
                    "workers": [
                        {
                            "name": "text@worker",
                            "queues": ["text_queue"],
                            "concurrency": 6,
                            "active_tasks": 1,
                        }
                    ],
                    "error": None,
                },
            }

    monkeypatch.setattr(task_module, "GenerationRuntimeMonitor", FakeRuntimeMonitor)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        active_session = session
        summary = await GenerationTaskService().task_summary(session)

    assert summary["execution_backend"] == "celery"
    assert summary["redis_queues"]["status"] == "ok"
    assert summary["redis_queues"]["queues"]["text_queue"]["depth"] == 2
    assert summary["worker_health"]["online_count"] == 1
    assert summary["worker_health"]["workers"][0]["name"] == "text@worker"

    await engine.dispose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_generation_task_service_reuses_active_duplicate_task() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-idempotent-task-1",
            name="Idempotent Task Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

        service = GenerationTaskService()
        first_task = await service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id=campaign.id,
            campaign_id=campaign.id,
            payload={
                "campaign_id": campaign.id,
                "limit": 3,
                "signals": {"country": "US", "platform": "Facebook"},
            },
        )
        duplicate_task = await service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id=campaign.id,
            campaign_id=campaign.id,
            payload={
                "signals": {"platform": "Facebook", "country": "US"},
                "limit": 3,
                "campaign_id": campaign.id,
            },
        )
        rows_after_duplicate = list(
            (await session.execute(select(GenerationTask))).scalars().all()
        )

        first_task.status = "succeeded"
        first_task.result_json = {"topics": [], "generated_count": 0}
        first_task.finished_at = utcnow()
        await session.commit()

        next_task = await service.create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id=campaign.id,
            campaign_id=campaign.id,
            payload={
                "campaign_id": campaign.id,
                "limit": 3,
                "signals": {"country": "US", "platform": "Facebook"},
            },
        )
        rows_after_completed = list(
            (await session.execute(select(GenerationTask))).scalars().all()
        )

    assert duplicate_task.id == first_task.id
    assert getattr(duplicate_task, "reused_existing", False) is True
    assert should_schedule_generation_task(duplicate_task) is False
    assert GenerationTaskRead.from_model(duplicate_task).reused_existing is True
    assert duplicate_task.metadata_json["idempotency_reuse_count"] == 1
    assert "idempotency_key" in duplicate_task.metadata_json
    assert len(rows_after_duplicate) == 1
    assert next_task.id != first_task.id
    assert getattr(next_task, "reused_existing", False) is False
    assert should_schedule_generation_task(next_task) is True
    assert len(rows_after_completed) == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_service_reuses_concurrent_duplicate_task(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tasks.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-idempotent-task-race-1",
            name="Idempotent Task Race Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

    service = GenerationTaskService()
    original_find = service._find_active_duplicate_task
    entered_empty_find_count = 0
    both_requests_checked_for_duplicates = asyncio.Event()

    async def delayed_find(*args, **kwargs):
        nonlocal entered_empty_find_count
        result = await original_find(*args, **kwargs)
        if result is not None:
            return result
        entered_empty_find_count += 1
        if entered_empty_find_count == 2:
            both_requests_checked_for_duplicates.set()
        try:
            await asyncio.wait_for(both_requests_checked_for_duplicates.wait(), timeout=0.05)
        except TimeoutError:
            pass
        return result

    monkeypatch.setattr(service, "_find_active_duplicate_task", delayed_find)

    async def create_duplicate_task() -> GenerationTask:
        async with session_factory() as session:
            return await service.create_task(
                session,
                queue_name=TEXT_QUEUE_NAME,
                task_type="topic_generate",
                business_type="campaign",
                business_id=campaign.id,
                campaign_id=campaign.id,
                payload={
                    "campaign_id": campaign.id,
                    "limit": 3,
                    "signals": {"country": "US", "platform": "Facebook"},
                },
            )

    first_task, duplicate_task = await asyncio.gather(
        create_duplicate_task(),
        create_duplicate_task(),
    )

    async with session_factory() as session:
        rows = list((await session.execute(select(GenerationTask))).scalars().all())

    assert first_task.id == duplicate_task.id
    reuse_flags = {
        getattr(first_task, "reused_existing", False),
        getattr(duplicate_task, "reused_existing", False),
    }
    assert reuse_flags == {
        False,
        True,
    }
    assert len(rows) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_processing_claims_queued_task_once(
    monkeypatch,
    tmp_path,
) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'claim.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-claim-task-1",
            name="Claim Task Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

        task = await GenerationTaskService().create_task(
            session,
            queue_name=TEXT_QUEUE_NAME,
            task_type="topic_generate",
            business_type="campaign",
            business_id=campaign.id,
            campaign_id=campaign.id,
            payload={"campaign_id": campaign.id, "limit": 3, "signals": {}},
        )

    service = GenerationTaskService()
    original_get_task = GenerationTaskService.get_task
    queued_reads = 0
    both_workers_read_queued = asyncio.Event()
    run_count = 0

    async def delayed_get_task(self, session, task_id: str) -> GenerationTask:
        nonlocal queued_reads
        task = await original_get_task(self, session, task_id)
        if task.status == "queued":
            queued_reads += 1
            if queued_reads == 2:
                both_workers_read_queued.set()
            try:
                await asyncio.wait_for(both_workers_read_queued.wait(), timeout=0.1)
            except TimeoutError:
                pass
        return task

    async def counted_run_task(self, session, task: GenerationTask) -> dict[str, str]:
        nonlocal run_count
        run_count += 1
        await asyncio.sleep(0.05)
        return {"ok": task.id}

    monkeypatch.setattr(GenerationTaskService, "get_task", delayed_get_task)
    monkeypatch.setattr(GenerationTaskService, "_run_task", counted_run_task)

    await asyncio.gather(
        service._process_task_body(task.id),
        service._process_task_body(task.id),
    )

    async with session_factory() as session:
        stored = await session.get(GenerationTask, task.id)

    assert stored is not None
    assert stored.status == "succeeded"
    assert stored.attempt_count == 1
    assert run_count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_task_auto_retries_retryable_provider_failure(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_AUTO_RETRY_ENABLED", "true")
    get_settings.cache_clear()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auto-retry.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scheduled: list[tuple[str, str, int, int]] = []

    def capture_schedule(task, countdown_seconds: int) -> None:
        scheduled.append((task.id, task.queue_name, task.priority, countdown_seconds))

    monkeypatch.setattr(task_module, "_schedule_auto_retry_task", capture_schedule)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-auto-retry-task-1",
            name="Auto Retry Task Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

        task = await GenerationTaskService().create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-auto-retry-task-1",
            campaign_id=campaign.id,
            payload={"draft_id": "draft-auto-retry-task-1"},
            priority=4,
            max_attempts=3,
        )
        task.status = "running"
        task.attempt_count = 1
        task.started_at = utcnow() - timedelta(seconds=5)
        await session.commit()

        await GenerationTaskService()._mark_failed(
            session,
            task,
            TimeoutError("Gateway image API request timed out"),
        )
        stored = await session.get(GenerationTask, task.id)

    assert stored is not None
    assert stored.status == "queued"
    assert stored.retryable is False
    assert stored.error_code == "provider_timeout"
    assert stored.error_message == "Gateway image API request timed out"
    assert stored.started_at is None
    assert stored.finished_at is None
    assert stored.duration_ms is None
    assert stored.queued_at > stored.created_at
    assert len(scheduled) == 1
    scheduled_task_id, scheduled_queue_name, scheduled_priority, scheduled_delay = scheduled[0]
    assert scheduled_task_id == stored.id
    assert scheduled_queue_name == IMAGE_QUEUE_NAME
    assert scheduled_priority == 4
    assert 10 <= scheduled_delay <= 13
    assert stored.metadata_json["auto_retry"]["status"] == "scheduled"
    assert stored.metadata_json["auto_retry"]["next_attempt"] == 2
    assert stored.metadata_json["auto_retry"]["max_attempts"] == 3
    assert stored.metadata_json["auto_retry"]["remaining_attempts"] == 2
    assert stored.metadata_json["auto_retry"]["delay_seconds"] == scheduled_delay
    assert stored.metadata_json["auto_retry"]["last_error_code"] == "provider_timeout"

    await engine.dispose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_generation_task_auto_retries_gateway_provider_error(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_AUTO_RETRY_ENABLED", "true")
    monkeypatch.setenv("GENERATION_TASK_AUTO_RETRY_DELAYS_SECONDS", "0")
    get_settings.cache_clear()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'auto-retry-provider-error.sqlite'}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    scheduled: list[tuple[str, str, int, int]] = []

    def capture_schedule(task, countdown_seconds: int) -> None:
        scheduled.append((task.id, task.queue_name, task.priority, countdown_seconds))

    monkeypatch.setattr(task_module, "_schedule_auto_retry_task", capture_schedule)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-auto-retry-provider-error-1",
            name="Auto Retry Provider Error Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

        task = await GenerationTaskService().create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="external_image_generate",
            business_type="external_image",
            business_id="external-image-auto-retry-1",
            campaign_id=campaign.id,
            payload={"prompt": "slow image", "count": 1, "size": "1:1"},
            priority=2,
            max_attempts=3,
        )
        task.status = "running"
        task.attempt_count = 1
        task.started_at = utcnow() - timedelta(seconds=5)
        await session.commit()

        await GenerationTaskService()._mark_failed(
            session,
            task,
            ProviderError("Gateway image API returned HTTP 502: bad gateway"),
        )
        stored = await session.get(GenerationTask, task.id)

    assert stored is not None
    assert stored.status == "queued"
    assert stored.retryable is False
    assert stored.error_code == "unknown_provider_error"
    assert scheduled == [(stored.id, IMAGE_QUEUE_NAME, 2, 0)]
    assert stored.metadata_json["auto_retry"]["status"] == "scheduled"
    assert stored.metadata_json["auto_retry"]["next_attempt"] == 2

    await engine.dispose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_model_provider_capacity_limits_image_work(
    monkeypatch,
) -> None:
    monkeypatch.setenv("MODEL_PROVIDER_IMAGE_CONCURRENCY", "1")
    get_settings.cache_clear()
    task_module._reset_model_provider_capacity_for_tests()
    active_count = 0
    max_active_count = 0

    async def run_work() -> None:
        nonlocal active_count, max_active_count
        async with task_module._model_provider_capacity(IMAGE_QUEUE_NAME):
            active_count += 1
            max_active_count = max(max_active_count, active_count)
            await asyncio.sleep(0.02)
            active_count -= 1

    await asyncio.gather(run_work(), run_work(), run_work())

    assert max_active_count == 1

    task_module._reset_model_provider_capacity_for_tests()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_image_generation_task_does_not_hold_model_provider_capacity(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("MODEL_PROVIDER_IMAGE_CONCURRENCY", "1")
    get_settings.cache_clear()
    task_module._reset_model_provider_capacity_for_tests()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'image-capacity.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    active_count = 0
    max_active_count = 0

    async def fake_run_image_task(self, session, task) -> dict:
        nonlocal active_count, max_active_count
        active_count += 1
        max_active_count = max(max_active_count, active_count)
        try:
            await asyncio.sleep(0.03)
            return {"task_id": task.id, "generated_count": 1, "assets": []}
        finally:
            active_count -= 1

    monkeypatch.setattr(GenerationTaskService, "_run_image_task", fake_run_image_task)

    async with session_factory() as session:
        service = GenerationTaskService()
        first_task = await service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-image-capacity-1",
            payload={"draft_id": "draft-image-capacity-1"},
        )
        second_task = await service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-image-capacity-2",
            payload={"draft_id": "draft-image-capacity-2"},
        )

    await asyncio.gather(
        GenerationTaskService().process_task(first_task.id),
        GenerationTaskService().process_task(second_task.id),
    )

    assert max_active_count == 2

    await engine.dispose()
    task_module._reset_model_provider_capacity_for_tests()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_keyframe_task_batch_prepares_briefs_once_and_splits_scheme_tasks(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "")
    get_settings.cache_clear()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'keyframe-batch.sqlite'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    await _seed_generation_task_users(session_factory)
    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-keyframe-batch-1",
            name="Keyframe Batch Campaign",
            metadata_json={},
        )
        topic = ContentTopic(
            id="topic-keyframe-batch-1",
            campaign_id=campaign.id,
            title="Batch topic",
            angle="Batch angle",
            source_data={},
        )
        draft = CopyDraft(
            id="draft-keyframe-batch-1",
            campaign_id=campaign.id,
            topic_id=topic.id,
            body="Ad copy for keyframes",
            headline="Batch headline",
            metadata_json={},
        )
        session.add_all([campaign, topic, draft])
        await session.commit()

    brief_calls: list[int] = []

    class CountingImageTaskLLMProvider(FakeImageTaskLLMProvider):
        async def generate_image_briefs(
            self,
            draft: CopyDraft,
            count: int,
            size: str,
            feedback: str | None = None,
            source_asset: CreativeAsset | None = None,
            storyboard_context: dict | None = None,
        ) -> list[ImageBrief]:
            brief_calls.append(count)
            return await super().generate_image_briefs(
                draft=draft,
                count=count,
                size=size,
                feedback=feedback,
                source_asset=source_asset,
                storyboard_context=storyboard_context,
            )

    scheduled_task_ids: list[str] = []

    def capture_schedule(task, background_tasks=None):
        scheduled_task_ids.append(task.id)
        return True

    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(
        creative_service,
        "get_llm_provider",
        lambda settings: CountingImageTaskLLMProvider(),
    )
    monkeypatch.setattr(creatives_endpoint, "service", creative_service.CreativeService())
    monkeypatch.setattr(creatives_endpoint, "schedule_generation_task", capture_schedule)

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/creatives/generate/keyframe-tasks",
                headers=_operator_headers(OPERATOR_A_ID),
                json={
                    "draft_id": "draft-keyframe-batch-1",
                    "count": 6,
                    "size": "9:16",
                    "generation_mode": "video_keyframe_variants",
                    "variant_count": 3,
                    "frames_per_variant": 2,
                    "video_duration_seconds": 12,
                    "storyboard": [
                        {
                            "scene_index": 1,
                            "visual": "Open with product proof.",
                            "subtitle": "Win faster",
                        }
                    ],
                    "storyboard_text": "Scene 1: Open with product proof.",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202
    payload = response.json()
    assert [item["queue_name"] for item in payload] == [
        IMAGE_QUEUE_NAME,
        IMAGE_QUEUE_NAME,
        IMAGE_QUEUE_NAME,
    ]
    assert brief_calls == [6]

    async with session_factory() as session:
        tasks = list(
            (
                await session.execute(
                    select(GenerationTask).order_by(GenerationTask.created_at.asc())
                )
            )
            .scalars()
            .all()
        )

    assert [task.id for task in tasks] == scheduled_task_ids
    assert [task.payload_json["target_indices"] for task in tasks] == [
        [1, 2],
        [3, 4],
        [5, 6],
    ]
    assert [task.payload_json["count"] for task in tasks] == [2, 2, 2]
    assert [
        [brief["image_index"] for brief in task.payload_json["prepared_briefs"]]
        for task in tasks
    ] == [[1, 2], [3, 4], [5, 6]]

    await engine.dispose()
    get_settings.cache_clear()


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
async def test_generation_task_startup_recovery_schedules_celery_queue(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    enqueued: list[tuple[str, str, int]] = []

    def capture_enqueue(task_id: str, queue_name: str, priority: int) -> None:
        enqueued.append((task_id, queue_name, priority))

    async def no_op_process_task(self, task_id: str) -> None:
        return None

    monkeypatch.setattr(dispatcher, "_enqueue_celery_generation_task", capture_enqueue)
    monkeypatch.setattr(GenerationTaskService, "process_task", no_op_process_task)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-startup-celery-recovery-1",
            name="Startup Celery Recovery Campaign",
            metadata_json={},
        )
        session.add(campaign)
        await session.commit()

        queued_task = await GenerationTaskService().create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type="image_generate",
            business_type="copy_draft",
            business_id="draft-startup-celery-recovery-1",
            campaign_id=campaign.id,
            payload={"draft_id": "draft-startup-celery-recovery-1", "count": 2, "size": "1:1"},
            priority=7,
        )

    recovery = await recover_generation_tasks_on_startup()

    assert recovery.rescheduled_task_ids == [queued_task.id]
    assert enqueued == [(queued_task.id, IMAGE_QUEUE_NAME, 7)]

    await engine.dispose()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_generation_task_startup_recovery_keeps_running_tasks_in_celery_mode(
    monkeypatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    enqueued: list[tuple[str, str, int]] = []

    def capture_enqueue(task_id: str, queue_name: str, priority: int) -> None:
        enqueued.append((task_id, queue_name, priority))

    monkeypatch.setattr(dispatcher, "_enqueue_celery_generation_task", capture_enqueue)

    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-startup-celery-running-1",
            name="Startup Celery Running Campaign",
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
            queue_name=VIDEO_QUEUE_NAME,
            task_type="video_generate",
            business_type="video_asset",
            business_id="video-startup-celery-running-1",
            campaign_id=campaign.id,
            payload={"video_id": "video-startup-celery-running-1"},
        )
        running_task.status = "running"
        running_task.attempt_count = 1
        running_task.started_at = utcnow() - timedelta(minutes=5)
        await session.commit()

    recovery = await recover_generation_tasks_on_startup()

    async with session_factory() as session:
        stored_running = await session.get(GenerationTask, running_task.id)

    assert recovery.rescheduled_task_ids == [queued_task.id]
    assert recovery.interrupted_task_ids == []
    assert enqueued == [(queued_task.id, TEXT_QUEUE_NAME, 0)]
    assert stored_running is not None
    assert stored_running.status == "running"
    assert stored_running.error_code is None

    await engine.dispose()
    get_settings.cache_clear()


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
