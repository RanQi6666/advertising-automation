from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.db.models.generation_attempt import GenerationAttempt
from backend.app.db.session import get_session
from backend.app.main import create_app
from backend.app.services.generation_attempt_service import GenerationAttemptService


async def _events() -> AsyncIterator[dict]:
    yield {"type": "start", "limit": 3}
    yield {"type": "topic", "index": 1, "topic": {"id": "topic-1"}}
    yield {"type": "error", "index": 2, "message": "provider timeout"}
    yield {"type": "done", "generated": 1}


@pytest.mark.asyncio
async def test_generation_attempt_tracks_partial_stream_results() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = GenerationAttemptService()
        attempt = await service.create_attempt(
            session,
            business_type="topic",
            business_id="campaign-1",
            campaign_id="campaign-1",
            stage="topic_generation",
            total_count=3,
            metadata={"source": "stream"},
        )

        events = [
            event
            async for event in service.track_stream(
                session=session,
                attempt=attempt,
                events=_events(),
                success_event_types={"topic"},
            )
        ]

        stored = await session.get(GenerationAttempt, attempt.id)

    assert events[0]["type"] == "start"
    assert events[0]["attempt_id"] == attempt.id
    assert events[0]["total_count"] == 3
    assert stored is not None
    assert stored.status == "partial_succeeded"
    assert stored.success_count == 1
    assert stored.failed_count == 2
    assert stored.error_code == "provider_timeout"
    assert stored.error_message == "provider timeout"
    assert stored.finished_at is not None
    assert stored.duration_ms is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_generation_attempt_status_endpoint_returns_latest_state(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'attempts.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        attempt = await GenerationAttemptService().create_attempt(
            session,
            business_type="image",
            business_id="draft-1",
            campaign_id="campaign-1",
            stage="creative_image_generation",
            total_count=6,
        )
        await GenerationAttemptService().finish_attempt(
            session,
            attempt,
            success_count=6,
            failed_count=0,
        )

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/generation-attempts/{attempt.id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == attempt.id
    assert payload["status"] == "succeeded"
    assert payload["business_type"] == "image"
    assert payload["total_count"] == 6
    assert payload["success_count"] == 6
    assert payload["failed_count"] == 0

    await engine.dispose()
