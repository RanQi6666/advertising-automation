import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base, utcnow
from backend.app.db.models.ad_generation_job import AdGenerationJob
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.db.models.user import User
from backend.app.db.session import get_session
from backend.app.main import create_app

OPERATOR_A_ID = "00000000-0000-4000-8000-000000000101"
OPERATOR_B_ID = "00000000-0000-4000-8000-000000000102"
ADMIN_ID = "00000000-0000-4000-8000-000000000199"


@pytest.fixture(autouse=True)
def disable_access_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _client_with_db(tmp_path, filename: str):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / filename).as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    return client, engine, app, session_factory


async def _seed_users(session_factory) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                User(
                    id=OPERATOR_A_ID,
                    email="operator-a@example.test",
                    full_name="操作员A",
                    role="operator",
                    is_active=True,
                ),
                User(
                    id=OPERATOR_B_ID,
                    email="operator-b@example.test",
                    full_name="操作员B",
                    role="operator",
                    is_active=True,
                ),
                User(
                    id=ADMIN_ID,
                    email="admin@example.test",
                    full_name="管理员",
                    role="admin",
                    is_active=True,
                ),
            ]
        )
        await session.commit()


def _operator_headers(operator_id: str) -> dict[str, str]:
    return {"X-Operator-Id": operator_id}


@pytest.mark.asyncio
async def test_ad_generation_workbench_filters_claims_and_blocks_stale_updates(tmp_path) -> None:
    client, engine, app, session_factory = await _client_with_db(
        tmp_path, "ad-generation-collaboration.db"
    )
    await _seed_users(session_factory)

    async with session_factory() as session:
        owned_by_a = AdGenerationJob(
            external_order_id="owned-a",
            status="fields_review",
            request_payload={},
            result_payload={"status": "fields_review"},
            metadata_json={},
            owner_user_id=OPERATOR_A_ID,
            locked_by=OPERATOR_A_ID,
            locked_at=utcnow(),
        )
        owned_by_b = AdGenerationJob(
            external_order_id="owned-b",
            status="fields_review",
            request_payload={},
            result_payload={"status": "fields_review"},
            metadata_json={},
            owner_user_id=OPERATOR_B_ID,
            locked_by=OPERATOR_B_ID,
            locked_at=utcnow(),
        )
        unassigned = AdGenerationJob(
            external_order_id="unassigned",
            status="fields_review",
            request_payload={},
            result_payload={"status": "fields_review"},
            metadata_json={},
        )
        session.add_all([owned_by_a, owned_by_b, unassigned])
        await session.commit()
        await session.refresh(owned_by_a)
        await session.refresh(owned_by_b)
        await session.refresh(unassigned)
        current_updated_at = owned_by_a.updated_at.isoformat()

    try:
        list_response = client.get(
            "/api/v1/integrations/publishing/ad-generation/jobs",
            headers=_operator_headers(OPERATOR_A_ID),
        )
        claim_response = client.post(
            f"/api/v1/integrations/publishing/ad-generation/jobs/{unassigned.id}/claim",
            headers=_operator_headers(OPERATOR_A_ID),
        )
        forbidden_response = client.patch(
            f"/api/v1/integrations/publishing/ad-generation/jobs/{owned_by_b.id}/review",
            headers=_operator_headers(OPERATOR_A_ID),
            json={
                "result_payload": {"status": "topic_review"},
                "expected_updated_at": owned_by_b.updated_at.isoformat(),
            },
        )
        stale_response = client.patch(
            f"/api/v1/integrations/publishing/ad-generation/jobs/{owned_by_a.id}/review",
            headers=_operator_headers(OPERATOR_A_ID),
            json={
                "result_payload": {"status": "topic_review"},
                "expected_updated_at": "2000-01-01T00:00:00+00:00",
            },
        )
        update_response = client.patch(
            f"/api/v1/integrations/publishing/ad-generation/jobs/{owned_by_a.id}/review",
            headers=_operator_headers(OPERATOR_A_ID),
            json={
                "result_payload": {"status": "topic_review"},
                "expected_updated_at": current_updated_at,
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert list_response.status_code == 200
    listed_ids = {item["id"] for item in list_response.json()}
    assert listed_ids == {owned_by_a.id, unassigned.id}

    assert claim_response.status_code == 200
    claimed = claim_response.json()
    assert claimed["owner_user_id"] == OPERATOR_A_ID
    assert claimed["locked_by"] == OPERATOR_A_ID
    assert claimed["can_edit"] is True

    assert forbidden_response.status_code == 403
    assert stale_response.status_code == 409
    assert update_response.status_code == 200
    assert update_response.json()["status"] == "topic_review"

    await engine.dispose()


@pytest.mark.asyncio
async def test_ad_performance_workbench_filters_and_allows_admin_delete(tmp_path) -> None:
    client, engine, app, session_factory = await _client_with_db(
        tmp_path, "ad-performance-collaboration.db"
    )
    await _seed_users(session_factory)

    async with session_factory() as session:
        owned_by_a = AdPerformanceAnalysis(
            source_type="external",
            status="completed",
            creative_name="creative-a",
            request_payload={},
            metrics={},
            analysis_result={
                "summary": "a",
                "confidence": "low",
                "analysis_mode": "llm_only",
            },
            owner_user_id=OPERATOR_A_ID,
            locked_by=OPERATOR_A_ID,
            locked_at=utcnow(),
        )
        owned_by_b = AdPerformanceAnalysis(
            source_type="external",
            status="completed",
            creative_name="creative-b",
            request_payload={},
            metrics={},
            analysis_result={
                "summary": "b",
                "confidence": "low",
                "analysis_mode": "llm_only",
            },
            owner_user_id=OPERATOR_B_ID,
            locked_by=OPERATOR_B_ID,
            locked_at=utcnow(),
        )
        unassigned = AdPerformanceAnalysis(
            source_type="external",
            status="completed",
            creative_name="creative-unassigned",
            request_payload={},
            metrics={},
            analysis_result={
                "summary": "unassigned",
                "confidence": "low",
                "analysis_mode": "llm_only",
            },
        )
        session.add_all([owned_by_a, owned_by_b, unassigned])
        await session.commit()
        await session.refresh(owned_by_a)
        await session.refresh(owned_by_b)
        await session.refresh(unassigned)

    try:
        operator_list_response = client.get(
            "/api/v1/integrations/ad-performance/analyses",
            headers=_operator_headers(OPERATOR_A_ID),
        )
        admin_list_response = client.get(
            "/api/v1/integrations/ad-performance/analyses",
            headers=_operator_headers(ADMIN_ID),
        )
        forbidden_delete_response = client.delete(
            f"/api/v1/integrations/ad-performance/analyses/{owned_by_b.id}",
            headers=_operator_headers(OPERATOR_A_ID),
        )
        admin_delete_response = client.delete(
            f"/api/v1/integrations/ad-performance/analyses/{owned_by_b.id}",
            headers=_operator_headers(ADMIN_ID),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert operator_list_response.status_code == 200
    operator_ids = {item["analysis_id"] for item in operator_list_response.json()}
    assert operator_ids == {owned_by_a.id, unassigned.id}

    assert admin_list_response.status_code == 200
    admin_ids = {item["analysis_id"] for item in admin_list_response.json()}
    assert admin_ids == {owned_by_a.id, owned_by_b.id, unassigned.id}

    assert forbidden_delete_response.status_code == 403
    assert admin_delete_response.status_code == 204

    await engine.dispose()


@pytest.mark.asyncio
async def test_operator_list_seeds_lightweight_default_users(tmp_path) -> None:
    client, engine, app, _session_factory = await _client_with_db(
        tmp_path, "operator-list.db"
    )

    try:
        response = client.get("/api/v1/operators")
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 200
    operators = response.json()
    assert [operator["role"] for operator in operators] == ["operator", "operator", "admin"]
    assert operators[0]["full_name"] == "操作员001"
    assert operators[2]["full_name"] == "管理员"

    await engine.dispose()
