import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.api.v1.endpoints.ad_research as endpoint
from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.session import get_session
from backend.app.main import create_app


def _payload() -> dict:
    return {
        "external_user_id": "external-research-001",
        "country": "IN",
        "category": "gambling",
        "keywords": ["rummy", "casino"],
        "target_count": 25,
    }


def test_create_replay_conflict_and_poll(monkeypatch) -> None:
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "")
    monkeypatch.setattr(endpoint, "schedule_ad_research_job", lambda *args, **kwargs: True)
    get_settings.cache_clear()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    async def initialize() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    asyncio.run(initialize())
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    created = client.post("/api/v1/integrations/ad-research/jobs", json=_payload())
    assert created.status_code == 202
    task_id = created.json()["task_id"]
    assert created.json()["poll_url"].endswith(task_id)
    replay = client.post("/api/v1/integrations/ad-research/jobs", json=_payload())
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    changed = _payload()
    changed["category"] = "game"
    conflict = client.post("/api/v1/integrations/ad-research/jobs", json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "external_user_id_payload_conflict"
    poll = client.get(f"/api/v1/integrations/ad-research/jobs/{task_id}")
    assert poll.status_code == 200
    assert poll.json()["status"] == "queued"
    app.dependency_overrides.clear()
    asyncio.run(engine.dispose())
