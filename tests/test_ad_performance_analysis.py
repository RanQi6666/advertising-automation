import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.db.session import get_session
from backend.app.main import create_app
from backend.app.schemas.ad_performance import AdPerformanceAnalysisCreate
from backend.app.services.ad_performance_analysis_service import AdPerformanceAnalysisService


@pytest.fixture(autouse=True)
def disable_access_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _campaign_payload() -> dict:
    return {
        "id": "23",
        "name": "new1",
        "fb_id": "120247498350000238",
        "status": "ACTIVE",
        "objective": "OUTCOME_TRAFFIC",
    }


def _adset_payload() -> dict:
    return {
        "id": "19",
        "name": "new1",
        "fb_id": "120247498370850238",
        "status": "ACTIVE",
        "campaign_db_id": 23,
        "daily_budget": "100",
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "LINK_CLICKS",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "countries": "US",
        "age_min": 18,
        "age_max": 65,
        "campaign_name": "new1",
    }


def _new12_payload() -> dict:
    return {
        "source_type": "external",
        "external_user_id": "5",
        "date_start": "2026-06-15",
        "date_stop": "2026-06-21",
        "campaign": _campaign_payload(),
        "adset": _adset_payload(),
        "creative": {
            "id": "19",
            "name": "new12",
            "facebook_ad_id": "120247505013060238",
            "ad_status": "PAUSED",
            "message": "My record: 3 minutes. Can you beat it?",
            "link": "https://gmini.messrocts.com/slash-the-hordes/",
            "asset_id": 18,
            "asset_name": "photo_2026-06-16_22-30-35.jpg",
            "asset_type": "image",
            "creative_type": "image",
        },
        "insight": {
            "campaign_id": "120247498350000238",
            "campaign_name": "new1",
            "adset_id": "120247498370850238",
            "adset_name": "new1",
            "ad_id": "120247505013060238",
            "ad_name": "new12",
            "spend": "0.24",
            "impressions": "1079",
            "reach": "937",
            "frequency": "1.151547",
            "clicks": "83",
            "inline_link_clicks": "86",
            "ctr": "7.692308",
            "inline_link_click_ctr": "7.970343",
            "cpc": "0.002892",
            "cpm": "0.222428",
            "actions": [
                {"action_type": "link_click", "value": "86"},
                {"action_type": "landing_page_view", "value": "23"},
                {"action_type": "post_engagement", "value": "87"},
            ],
            "date_start": "2026-06-15",
            "date_stop": "2026-06-21",
        },
    }


def _new10_payload() -> dict:
    return {
        "source_type": "external",
        "external_user_id": "5",
        "campaign": _campaign_payload(),
        "adset": _adset_payload(),
        "creative": {
            "id": "20",
            "name": "new10",
            "facebook_ad_id": "120247505956440238",
            "ad_status": "PAUSED",
            "asset_type": "video",
            "creative_type": "video",
        },
        "insight": {
            "campaign_id": "120247498350000238",
            "campaign_name": "new1",
            "adset_id": "120247498370850238",
            "adset_name": "new1",
            "ad_id": "120247505956440238",
            "ad_name": "new10",
            "spend": "0",
            "impressions": "19",
            "reach": "19",
            "frequency": "1",
            "clicks": "0",
            "inline_link_clicks": "0",
            "ctr": "0",
            "video_play_actions": [{"action_type": "video_view", "value": "13"}],
            "video_p25_watched_actions": [{"action_type": "video_view", "value": "5"}],
            "video_p50_watched_actions": [{"action_type": "video_view", "value": "3"}],
            "actions": [
                {"action_type": "post_engagement", "value": "6"},
                {"action_type": "page_engagement", "value": "6"},
                {"action_type": "video_view", "value": "6"},
            ],
            "date_start": "2026-06-15",
            "date_stop": "2026-06-21",
        },
    }


@pytest.mark.asyncio
async def test_ad_performance_analysis_detects_post_click_drop() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        analysis = await AdPerformanceAnalysisService().create_analysis(
            session,
            AdPerformanceAnalysisCreate.model_validate(_new12_payload()),
        )

    problem_codes = {item["code"] for item in analysis.analysis_result["problems"]}

    assert analysis.creative_external_id == "120247505013060238"
    assert analysis.creative_name == "new12"
    assert analysis.metrics["landing_page_view_rate"] == pytest.approx(23 / 86)
    assert analysis.analysis_result["confidence"] == "high"
    assert "post_click_drop" in problem_codes
    assert "traffic_optimization_warning" in problem_codes

    await engine.dispose()


@pytest.mark.asyncio
async def test_ad_performance_analysis_keeps_low_sample_low_confidence() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        analysis = await AdPerformanceAnalysisService().create_analysis(
            session,
            AdPerformanceAnalysisCreate.model_validate(_new10_payload()),
        )

    problem_codes = {item["code"] for item in analysis.analysis_result["problems"]}

    assert analysis.creative_name == "new10"
    assert analysis.metrics["video_p50_rate"] == pytest.approx(3 / 13)
    assert analysis.analysis_result["confidence"] == "low"
    assert "insufficient_data" in problem_codes
    assert "low_ctr" not in problem_codes

    await engine.dispose()


@pytest.mark.asyncio
async def test_ad_performance_analysis_api_create_get_and_list(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'ad-performance.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            create_response = client.post(
                "/api/v1/integrations/ad-performance/analyses",
                json=_new12_payload(),
            )
            assert create_response.status_code == 201
            created = create_response.json()

            get_response = client.get(
                f"/api/v1/integrations/ad-performance/analyses/{created['analysis_id']}"
            )
            list_response = client.get("/api/v1/integrations/ad-performance/analyses")
    finally:
        app.dependency_overrides.clear()

    assert get_response.status_code == 200
    assert get_response.json()["analysis_id"] == created["analysis_id"]
    assert list_response.status_code == 200
    assert [item["analysis_id"] for item in list_response.json()] == [created["analysis_id"]]

    async with session_factory() as session:
        stored = await session.get(AdPerformanceAnalysis, created["analysis_id"])
        assert stored is not None
        assert stored.request_payload["creative"]["name"] == "new12"

    await engine.dispose()
