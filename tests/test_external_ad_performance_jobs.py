import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.api.v1.endpoints.ad_performance as ad_performance_endpoint
import backend.app.services.generation_task_service as task_module
from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.db.session import get_session
from backend.app.main import create_app


def _payload(external_request_id="job-1") -> dict:
    return {
        "external_request_id": external_request_id,
        "source_type": "external",
        "campaign": {"objective": "OUTCOME_TRAFFIC", "name": "new1"},
        "adset": {"optimization_goal": "LINK_CLICKS", "countries": "US"},
        "creative": {
            "creative_type": "image",
            "image_url": "https://newpixel.messrocts.com/uploads/a.jpg",
            "message": "My record: 3 minutes. Can you beat it?",
        },
        "insight": {
            "status": "ACTIVE",
            "spend": "0.24",
            "impressions": "1079",
            "inline_link_clicks": "86",
            "actions": [
                {"action_type": "link_click", "value": "86"},
                {"action_type": "landing_page_view", "value": "23"},
            ],
        },
        "siblings": [],
    }


def test_async_job_create_idempotency_conflict_and_poll(monkeypatch):
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "background_tasks")
    monkeypatch.setenv("PUBLIC_RESEARCH_ENABLED", "false")
    get_settings.cache_clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)

    async def override_session():
        async with session_factory() as session:
            yield session

    async def init_models():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(init_models())

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)

    created = client.post("/api/v1/integrations/ad-performance/analysis-jobs", json=_payload())
    assert created.status_code == 202
    body = created.json()
    assert body["code"] == 1001
    analysis_id = body["data"]["analysis_id"]
    assert body["data"]["status"] in {"queued", "processing", "succeeded"}
    assert body["data"]["poll_url"].endswith(analysis_id)

    replay = client.post("/api/v1/integrations/ad-performance/analysis-jobs", json=_payload())
    assert replay.status_code == 200
    assert replay.json()["code"] == 0
    assert replay.json()["data"]["analysis_id"] == analysis_id
    assert replay.json()["data"]["idempotent_replay"] is True

    changed = _payload()
    changed["insight"]["spend"] = "9.99"
    conflict = client.post("/api/v1/integrations/ad-performance/analysis-jobs", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == 4001

    polled = client.get(f"/api/v1/integrations/ad-performance/analysis-jobs/{analysis_id}")
    assert polled.status_code == 200
    assert polled.json()["code"] == 0
    assert polled.json()["data"]["analysis_id"] == analysis_id

    missing = client.get("/api/v1/integrations/ad-performance/analysis-jobs/missing")
    assert missing.status_code == 404
    assert missing.json()["code"] == 4001

    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())
    get_settings.cache_clear()


def test_dispatch_failure_is_persisted_and_idempotent_retry_reschedules(monkeypatch):
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with session_factory() as session:
            yield session

    async def init_models():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def load_analysis():
        async with session_factory() as session:
            result = await session.execute(
                select(AdPerformanceAnalysis).where(
                    AdPerformanceAnalysis.external_request_id == "dispatch-retry-1"
                )
            )
            return result.scalar_one()

    asyncio.run(init_models())
    calls = {"count": 0}

    def flaky_schedule(_task, _background_tasks):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("redis unavailable")
        return True

    monkeypatch.setattr(ad_performance_endpoint, "schedule_generation_task", flaky_schedule)
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    client = TestClient(app, raise_server_exceptions=False)

    first = client.post(
        "/api/v1/integrations/ad-performance/analysis-jobs",
        json=_payload("dispatch-retry-1"),
    )
    assert first.status_code == 503
    failed_dispatch = asyncio.run(load_analysis())
    analysis_id = failed_dispatch.analysis_id
    assert failed_dispatch.dispatch_attempts == 1
    assert failed_dispatch.last_dispatched_at is None
    assert "redis unavailable" in failed_dispatch.dispatch_error

    retry = client.post(
        "/api/v1/integrations/ad-performance/analysis-jobs",
        json=_payload("dispatch-retry-1"),
    )
    assert retry.status_code == 200
    assert retry.json()["data"]["analysis_id"] == analysis_id
    assert retry.json()["data"]["idempotent_replay"] is True
    successful_dispatch = asyncio.run(load_analysis())
    assert successful_dispatch.dispatch_attempts == 2
    assert successful_dispatch.last_dispatched_at is not None
    assert successful_dispatch.dispatch_error is None
    assert calls["count"] == 2

    normal_replay = client.post(
        "/api/v1/integrations/ad-performance/analysis-jobs",
        json=_payload("dispatch-retry-1"),
    )
    assert normal_replay.status_code == 200
    assert calls["count"] == 2

    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())
    get_settings.cache_clear()
