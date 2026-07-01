import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.models.topic import ContentTopic
from backend.app.services import generation_task_service as task_module
from backend.app.services.generation_task_service import TEXT_QUEUE_NAME, GenerationTaskService


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
