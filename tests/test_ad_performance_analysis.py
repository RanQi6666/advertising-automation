from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.ad_performance_analysis import AdPerformanceAnalysis
from backend.app.db.session import get_session
from backend.app.integrations.llm.openai_provider import _ad_performance_user_content
from backend.app.main import create_app
from backend.app.schemas.ad_performance import AdPerformanceAnalysisCreate
from backend.app.services.ad_performance_analysis_service import AdPerformanceAnalysisService


@pytest.fixture(autouse=True)
def disable_access_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
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

    assert analysis.creative_external_id == "120247505013060238"
    assert analysis.creative_name == "new12"
    assert analysis.metrics["landing_page_view_rate"] == pytest.approx(23 / 86)
    assert analysis.analysis_result["confidence"] == "medium"
    assert analysis.analysis_result["analysis_mode"] == "llm_only"
    assert analysis.analysis_result["ai_analysis"]["summary"]
    assert analysis.analysis_result["problems"] == []
    assert analysis.analysis_result["recommendations"] == []
    assert analysis.analysis_result["next_checks"] == []
    assert analysis.analysis_result["rule_summary"] is None
    work_order = analysis.analysis_result["optimization_work_order"]
    assert work_order == analysis.analysis_result["ai_analysis"]["optimization_work_order"]
    assert work_order["operator_summary"].startswith("Mock AI work order")
    campaign_actions = {item["field"]: item["action"] for item in work_order["campaign"]}
    adset_actions = {item["field"]: item["action"] for item in work_order["adset"]}
    creative_actions = {item["field"]: item["action"] for item in work_order["creative"]}
    assert work_order["overall_action"] == "check_landing_page_first"
    assert work_order["priority"] == "high"
    assert campaign_actions["objective"] == "rewrite"
    assert adset_actions["optimization_event"] == "missing"
    assert creative_actions["landing_page_url"] == "check"
    assert creative_actions["headline"] == "regenerate"
    assert analysis.analysis_result["data_completeness"]["level"] == "medium"
    assert "图片 URL 或缩略图" in analysis.analysis_result["data_completeness"]["missing"]
    assert "广告标题 headline" in analysis.analysis_result["data_completeness"]["missing"]
    assert (
        "转化事件 purchase/add_to_cart/lead"
        in analysis.analysis_result["data_completeness"]["missing"]
    )
    await engine.dispose()


@pytest.mark.asyncio
async def test_ad_performance_analysis_sends_media_fields_to_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class CapturingProvider:
        async def analyze_ad_performance(self, context: dict[str, Any]) -> dict[str, Any]:
            captured["context"] = context
            return {"summary": "media fields received"}

    monkeypatch.setattr(
        "backend.app.services.ad_performance_analysis_service.get_llm_provider",
        lambda settings=None: CapturingProvider(),
    )

    payload = _new12_payload()
    payload["creative"].update(
        {
            "headline": "My record: 3 minutes. Can you beat it?",
            "image_url": "https://cdn.example.com/new12.jpg",
            "thumbnail_url": "https://cdn.example.com/new12-thumb.jpg",
            "video_url": "https://cdn.example.com/new12.mp4",
            "video_keyframes": [{"second": 0, "image_url": "https://cdn.example.com/new12-0.jpg"}],
        }
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        analysis = await AdPerformanceAnalysisService().create_analysis(
            session,
            AdPerformanceAnalysisCreate.model_validate(payload),
        )

    creative_context = captured["context"]["creative"]

    assert analysis.analysis_result["analysis_mode"] == "llm_only"
    assert creative_context["headline"] == "My record: 3 minutes. Can you beat it?"
    assert creative_context["image_url"] == "https://cdn.example.com/new12.jpg"
    assert creative_context["thumbnail_url"] == "https://cdn.example.com/new12-thumb.jpg"
    assert creative_context["video_url"] == "https://cdn.example.com/new12.mp4"
    assert creative_context["video_keyframes"] == [
        {"second": 0, "image_url": "https://cdn.example.com/new12-0.jpg"}
    ]

    await engine.dispose()


def test_ad_performance_analysis_user_content_attaches_image_url() -> None:
    context = {
        "creative": {
            "creative_type": "image",
            "image_url": "https://cdn.example.com/new12.jpg",
            "message": "My record: 3 minutes. Can you beat it?",
        },
        "metrics": {"ctr": 7.69},
    }

    content = _ad_performance_user_content(context)

    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "My record: 3 minutes" in content[0]["text"]
    assert content[1] == {
        "type": "image_url",
        "image_url": {"url": "https://cdn.example.com/new12.jpg"},
    }


def test_ad_performance_analysis_user_content_attaches_video_url_for_video_provider() -> None:
    context = {
        "creative": {
            "creative_type": "video",
            "video_url": "https://cdn.example.com/new10.mp4",
            "message": "Watch the first three seconds.",
        },
        "metrics": {"video_p50_rate": 0.23},
    }

    content = _ad_performance_user_content(
        context,
        supports_video_input=True,
        video_fps=0.5,
    )

    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert "Watch the first three seconds" in content[0]["text"]
    assert content[1] == {
        "type": "video_url",
        "video_url": {"url": "https://cdn.example.com/new10.mp4", "fps": 0.5},
    }


def test_ad_performance_analysis_user_content_keeps_video_url_as_text_without_support() -> None:
    context = {
        "creative": {
            "creative_type": "video",
            "video_url": "https://cdn.example.com/new10.mp4",
        },
    }

    content = _ad_performance_user_content(context)

    assert isinstance(content, str)
    assert "https://cdn.example.com/new10.mp4" in content


@pytest.mark.asyncio
async def test_ad_performance_analysis_stores_visual_analysis_when_image_url_exists() -> None:
    payload = _new12_payload()
    payload["creative"]["image_url"] = "https://cdn.example.com/new12.jpg"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        analysis = await AdPerformanceAnalysisService().create_analysis(
            session,
            AdPerformanceAnalysisCreate.model_validate(payload),
        )

    visual_analysis = analysis.analysis_result["ai_analysis"]["visual_analysis"]

    assert visual_analysis["source_image_url"] == "https://cdn.example.com/new12.jpg"
    assert visual_analysis["summary"]
    assert visual_analysis["recommendations"]
    assert "图片 URL 或缩略图" in analysis.analysis_result["data_completeness"]["available"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_ad_performance_analysis_stores_visual_analysis_when_video_url_exists() -> None:
    payload = _new10_payload()
    payload["creative"]["video_url"] = "https://cdn.example.com/new10.mp4"

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        analysis = await AdPerformanceAnalysisService().create_analysis(
            session,
            AdPerformanceAnalysisCreate.model_validate(payload),
        )

    visual_analysis = analysis.analysis_result["ai_analysis"]["visual_analysis"]

    assert visual_analysis["source_video_url"] == "https://cdn.example.com/new10.mp4"
    assert visual_analysis["source_image_url"] is None
    assert visual_analysis["summary"]
    assert "视频 URL 或关键帧" in analysis.analysis_result["data_completeness"]["available"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_ad_performance_analysis_stream_updates_existing_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StreamingProvider:
        async def stream_ad_performance_analysis(self, context: dict[str, Any]):
            yield {"type": "delta", "text": '{"summary":'}
            yield {"type": "delta", "text": '"streamed analysis"}'}
            yield {
                "type": "done",
                "analysis": {
                    "summary": "streamed analysis",
                    "root_causes": ["landing page drop"],
                    "recommended_actions": ["check landing page speed"],
                    "next_tests": ["rerun with purchase events"],
                    "creative_feedback": ["image URL is available"],
                    "audience_feedback": [],
                    "landing_page_feedback": ["landing page view rate is low"],
                    "budget_delivery_feedback": [],
                    "risk_notes": ["sample is still limited"],
                    "optimization_work_order": {
                        "schema_version": "ad_performance_optimization_work_order_v1",
                        "operator_summary": "streamed AI work order",
                        "priority": "high",
                        "overall_action": "check_landing_page_first",
                        "next_step": "check landing page speed",
                        "modules_to_change": ["landing page URL"],
                        "modules_to_keep": [],
                        "modules_to_watch": [],
                        "campaign": [],
                        "adset": [],
                        "creative": [
                            {
                                "field": "landing_page_url",
                                "label": "landing page URL",
                                "current_value": "https://example.com",
                                "action": "check",
                                "priority": "high",
                                "suggested_value": "https://example.com",
                                "suggested_direction": None,
                                "generation_prompt": None,
                                "reason": "landing page view rate is low",
                                "source": "ai",
                                "can_apply_to_generation": False,
                                "missing": False,
                            }
                        ],
                        "warnings": [],
                    },
                    "confidence_note": "streamed result",
                },
            }

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    service = AdPerformanceAnalysisService()
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        analysis = await service.create_analysis(
            session,
            AdPerformanceAnalysisCreate.model_validate(_new12_payload()),
        )

        monkeypatch.setattr(
            "backend.app.services.ad_performance_analysis_service.get_llm_provider",
            lambda settings=None: StreamingProvider(),
        )

        events = [event async for event in service.stream_ai_analysis(session, analysis.id)]
        await session.refresh(analysis)

    assert [event["type"] for event in events] == ["start", "delta", "delta", "done"]
    assert events[1]["text"] == '{"summary":'
    assert analysis.analysis_result["analysis_mode"] == "llm_only"
    assert analysis.analysis_result["summary"] == "streamed analysis"
    assert analysis.analysis_result["ai_analysis"]["root_causes"] == ["landing page drop"]
    assert (
        analysis.analysis_result["optimization_work_order"]
        == analysis.analysis_result["ai_analysis"]["optimization_work_order"]
    )
    assert (
        analysis.analysis_result["optimization_work_order"]["operator_summary"]
        == "streamed AI work order"
    )
    assert analysis.analysis_result["llm_error"] is None
    assert analysis.error_message is None

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

    assert analysis.creative_name == "new10"
    assert analysis.metrics["video_p50_rate"] == pytest.approx(3 / 13)
    assert analysis.analysis_result["confidence"] == "low"
    assert analysis.analysis_result["analysis_mode"] == "llm_only"
    assert analysis.analysis_result["problems"] == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_ad_performance_analysis_falls_back_when_llm_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingProvider:
        async def analyze_ad_performance(self, context: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("temporary llm outage")

    monkeypatch.setattr(
        "backend.app.services.ad_performance_analysis_service.get_llm_provider",
        lambda settings=None: FailingProvider(),
    )

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        analysis = await AdPerformanceAnalysisService().create_analysis(
            session,
            AdPerformanceAnalysisCreate.model_validate(_new12_payload()),
        )

    assert analysis.analysis_result["analysis_mode"] == "llm_failed"
    assert analysis.analysis_result["ai_analysis"] is None
    assert analysis.analysis_result["llm_error"] == "temporary llm outage"
    assert analysis.error_message == "LLM analysis failed: temporary llm outage"
    assert analysis.analysis_result["problems"] == []
    assert analysis.analysis_result["recommendations"] == []

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


async def test_ad_performance_analysis_api_delete(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'ad-performance-delete.db').as_posix()}"
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

            delete_response = client.delete(
                f"/api/v1/integrations/ad-performance/analyses/{created['analysis_id']}"
            )
            missing_response = client.get(
                f"/api/v1/integrations/ad-performance/analyses/{created['analysis_id']}"
            )
            list_response = client.get("/api/v1/integrations/ad-performance/analyses")
    finally:
        app.dependency_overrides.clear()

    assert delete_response.status_code == 204
    assert missing_response.status_code == 404
    assert list_response.status_code == 200
    assert list_response.json() == []

    async with session_factory() as session:
        stored = await session.get(AdPerformanceAnalysis, created["analysis_id"])
        assert stored is None

    await engine.dispose()
