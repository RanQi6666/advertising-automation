import asyncio
import re
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.api.v1.endpoints.ad_research as endpoint
from backend.app.core.config import get_settings
from backend.app.db.base import Base, utcnow
from backend.app.db.models.ad_research_job import AdResearchJob
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


def test_ad_research_api_document_preserves_external_task_state_contract() -> None:
    document = (Path(__file__).resolve().parents[1] / "docs" / "ad-research-api.md").read_text(
        encoding="utf-8"
    )
    polling_section = re.search(
        r"^## 2\. 轮询任务结果\s*(.*?)(?=^### |^## |\Z)", document, re.MULTILINE | re.DOTALL
    )
    status_section = re.search(
        r"^## 任务状态与错误\s*(.*?)(?=^## |\Z)", document, re.MULTILINE | re.DOTALL
    )

    assert polling_section is not None
    assert status_section is not None
    polling_contract = polling_section.group(1)
    status_contract = status_section.group(1)

    assert re.search(
        r"新任务\s*只会.*?`queued`.*?`processing`.*?`completed`.*?`failed`.*?状态",
        polling_contract,
        re.DOTALL,
    )
    assert "insufficient" not in polling_contract

    legacy_row = re.search(r"^\|\s*`insufficient`\s*\|.*$", status_contract, re.MULTILINE)
    completed_row = re.search(r"^\|\s*`completed`\s*\|.*$", status_contract, re.MULTILINE)
    failed_row = re.search(r"^\|\s*`failed`\s*\|.*$", status_contract, re.MULTILINE)
    expired_row = re.search(
        r"^\|\s*`410 Gone`\s*/\s*`expired`\s*\|.*$", status_contract, re.MULTILINE
    )

    assert legacy_row is not None
    assert re.search(
        r"(?:历史.*?(?:只读|兼容)|legacy(?:-only)?)", legacy_row.group(), re.IGNORECASE
    )
    assert re.search(r"新任务.*?(?:绝不会|不会).*?(?:产生|出现)", legacy_row.group())
    assert not re.search(r"(?:partial|top\s*n)", legacy_row.group(), re.IGNORECASE)

    assert completed_row is not None
    assert re.search(r"严格.*?`target_count`", completed_row.group())
    assert "停止轮询" in completed_row.group()

    assert failed_row is not None
    assert re.search(r"`ads`\s*为\s*`null`", failed_row.group())
    assert "停止轮询" in failed_row.group()

    assert expired_row is not None
    assert re.search(r"GET.*?`410 Gone`", expired_row.group())


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


def test_poll_completed_returns_exact_ads_without_further_polling(monkeypatch) -> None:
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

    async def complete_as_completed(task_id: str) -> None:
        async with factory() as session:
            job = await session.get(AdResearchJob, task_id)
            assert job is not None
            await endpoint.service.complete_job(
                session,
                job,
                status="completed",
                ads=[{"ad_library_id": "ad-1"}, {"ad_library_id": "ad-2"}],
                summary={"selected_count": 2},
            )

    asyncio.run(initialize())
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    try:
        payload = _payload()
        payload["target_count"] = 2
        created = client.post("/api/v1/integrations/ad-research/jobs", json=payload)
        assert created.status_code == 202
        task_id = created.json()["task_id"]

        asyncio.run(complete_as_completed(task_id))
        poll = client.get(f"/api/v1/integrations/ad-research/jobs/{task_id}")

        assert poll.status_code == 200
        body = poll.json()
        assert body["status"] == "completed"
        assert body["ads"] == [{"ad_library_id": "ad-1"}, {"ad_library_id": "ad-2"}]
        assert len(body["ads"]) == payload["target_count"]
        assert body["poll_after_seconds"] is None
        assert body["error"] is None
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())



def test_poll_failed_returns_success_response_without_ads(monkeypatch) -> None:
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

    async def complete_as_failed(task_id: str) -> None:
        async with factory() as session:
            job = await session.get(AdResearchJob, task_id)
            assert job is not None
            await endpoint.service.complete_job(
                session,
                job,
                status="failed",
                ads=[],
                summary={"reason": "insufficient_qualified_ads"},
            )

    asyncio.run(initialize())
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    try:
        created = client.post("/api/v1/integrations/ad-research/jobs", json=_payload())
        assert created.status_code == 202
        task_id = created.json()["task_id"]

        asyncio.run(complete_as_failed(task_id))
        poll = client.get(f"/api/v1/integrations/ad-research/jobs/{task_id}")

        assert poll.status_code == 200
        assert poll.json()["status"] == "failed"
        assert poll.json()["poll_after_seconds"] is None
        assert poll.json()["result_expires_at"] is None
        assert poll.json()["ads"] is None
        assert poll.json()["error"] == {
            "code": "insufficient_qualified_ads",
            "message": "research could not produce the requested number of scored ads",
        }
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_poll_expired_results_return_gone_before_and_after_cleanup(monkeypatch) -> None:
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

    async def expire_results(*task_ids: str) -> None:
        async with factory() as session:
            for task_id in task_ids:
                job = await session.get(AdResearchJob, task_id)
                assert job is not None
                job.status = "completed"
                job.stage = "completed"
                job.result_json = {"ads": [{"ad_library_id": task_id}]}
                job.result_expires_at = utcnow() - timedelta(seconds=1)
            await session.commit()

    async def result_expiry_is_naive(task_id: str) -> bool:
        async with factory() as session:
            job = await session.get(AdResearchJob, task_id)
            assert job is not None
            assert job.result_expires_at is not None
            return job.result_expires_at.tzinfo is None

    async def cleanup() -> int:
        async with factory() as session:
            return await endpoint.service.cleanup_expired_results(session)

    asyncio.run(initialize())
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    client = TestClient(app)
    try:
        direct_payload = _payload()
        direct_payload["external_user_id"] = "expired-direct"
        cleanup_payload = _payload()
        cleanup_payload["external_user_id"] = "expired-cleanup"
        direct_response = client.post(
            "/api/v1/integrations/ad-research/jobs", json=direct_payload
        )
        direct_task_id = direct_response.json()["task_id"]
        cleanup_task_id = client.post(
            "/api/v1/integrations/ad-research/jobs", json=cleanup_payload
        ).json()["task_id"]
        asyncio.run(expire_results(direct_task_id, cleanup_task_id))

        assert asyncio.run(result_expiry_is_naive(direct_task_id))
        direct = client.get(f"/api/v1/integrations/ad-research/jobs/{direct_task_id}")
        assert direct.status_code == 410
        assert direct.json()["detail"]["code"] == "result_expired"

        assert asyncio.run(cleanup()) == 1
        cleaned = client.get(f"/api/v1/integrations/ad-research/jobs/{cleanup_task_id}")
        assert cleaned.status_code == 410
        assert cleaned.json()["detail"]["code"] == "result_expired"
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
