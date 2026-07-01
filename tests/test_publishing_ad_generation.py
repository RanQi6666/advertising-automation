import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.ad_generation_job import AdGenerationJob
from backend.app.db.models.agent_run import AgentRun
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.models.review import ReviewTask
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.db.models.work_order import WorkOrder
from backend.app.db.session import get_session
from backend.app.main import create_app
from backend.app.schemas.ad_generation import (
    PublishingAdGenerationJobCreate,
    PublishingAdGenerationPreferences,
    PublishingAdGenerationReviewConfirm,
    PublishingAdGenerationReviewUpdate,
    PublishingWorkOrderPayload,
)
from backend.app.services import generation_task_service as task_module
from backend.app.services.ad_generation_service import AdGenerationService
from backend.app.services.generation_task_service import GenerationTaskService


def _delivery_field(value, normalized_value=None, confidence: float = 1.0) -> dict:
    return {
        "value": value,
        "normalized_value": value if normalized_value is None else normalized_value,
        "status": "extracted",
        "confidence": confidence,
        "evidence": ["test"],
        "candidates": [value],
        "reason": "test fixture",
    }


def _delivery_extraction() -> dict:
    return {
        "schema_version": "ad_delivery_extract_v1",
        "fields": {
            "landing_url": _delivery_field("https://example.com/tv"),
            "event_name": _delivery_field("shopping"),
            "country": _delivery_field("India", "IN"),
            "age_min": _delivery_field(25),
            "age_max": _delivery_field(45),
            "gender": _delivery_field("male"),
            "audience_description_raw": _delivery_field("male age 25-45"),
        },
        "review": {"must_confirm": []},
    }


@pytest.fixture(autouse=True)
def mock_generation_providers(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_PROVIDER", "placeholder")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_publishing_ad_generation_builds_single_country_package() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-1",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: streaming TV app\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: purchase\n"
                        "Landing: https://example.com/product"
                    ),
                    structured_fields={
                        "project_name": "Streaming TV App",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "purchase",
                        "landing_url": "https://example.com/product",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )

        completed = await service.process_job(session, job.id)

    result = completed.result_payload
    campaign_payload = result["campaign_payload"]
    adset_payload = result["adset_payload"]
    creative_payload = result["creative_payload"]

    assert completed.status == "fields_review"
    assert result["status"] == "fields_review"
    assert campaign_payload["objective"] == "OUTCOME_SALES"
    assert campaign_payload["draft"] == 1
    assert adset_payload["countries"] == "US"
    assert isinstance(adset_payload["countries"], str)
    assert adset_payload["country_code"] == "US"
    assert adset_payload["age_min"] == 25
    assert adset_payload["age_max"] == 45
    assert adset_payload["event_name"] == "purchase"
    assert "pixel_id" not in adset_payload
    assert creative_payload is None
    assert result["assets"]["images"] == []
    assert result["metadata_json"]["campaign_id"]
    assert result["metadata_json"]["work_order_id"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_persists_generic_creative_strategy() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-strategy-v2",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: puzzle game\n"
                        "Country: Singapore\n"
                        "Audience: Female 25-34\n"
                        "Event: traffic\n"
                        "Landing: https://play.example.sg/level-challenge\n"
                        "Brief: Make a level challenge game ad."
                    ),
                    delivery_extraction=_delivery_extraction(),
                    structured_fields={
                        "product_name": "Puzzle Quest",
                        "country": "Singapore",
                        "landing_url": "https://play.example.sg/level-challenge",
                        "audience_description_raw": "Female 25-34",
                    },
                ),
            ),
        )
        completed = await service.process_job(session, job.id)
        campaign = await session.get(
            Campaign,
            completed.result_payload["metadata_json"]["campaign_id"],
        )

    assert campaign is not None
    strategy = campaign.metadata_json["creative_strategy"]
    assert strategy["schema_version"] == "creative_strategy.v2"
    assert strategy["vertical"] == "game"
    assert strategy["market_context"]["country_code"] == "SG"
    assert len({item["angle_type"] for item in strategy["topic_angle_plan"]}) == 3
    assert completed.result_payload["metadata_json"]["creative_strategy"]["schema_version"] == (
        "creative_strategy.v2"
    )

    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_result_endpoint_requires_returned_status(
    tmp_path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'adgen-result-pending.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-result-pending",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: pending result\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: purchase\n"
                        "Landing: https://example.com/pending"
                    ),
                    structured_fields={
                        "project_name": "Pending Result",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "purchase",
                        "landing_url": "https://example.com/pending",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/integrations/publishing/ad-generation/jobs/{job.id}/result"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json()["detail"] == "Final ad generation result is not ready."

    await engine.dispose()


def test_publishing_ad_generation_review_url_includes_access_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AD_GENERATION_REVIEW_BASE_URL", "https://ai.example.test")
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "test-review-token")
    get_settings.cache_clear()

    service = AdGenerationService()

    assert (
        service.review_url_for_job("job-123")
        == "https://ai.example.test/review/ad-generation/job-123?access_token=test-review-token"
    )


def test_publishing_ad_generation_return_url_uses_default_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_ADS_RETURN_URL", "https://ads.ggcss.xyz/api/ai/receive_ai_ads")
    get_settings.cache_clear()

    service = AdGenerationService()
    job = AdGenerationJob(metadata_json={})

    assert service.return_url_for_job(job) == "https://ads.ggcss.xyz/api/ai/receive_ai_ads"


def test_publishing_ad_generation_return_url_prefers_job_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_ADS_RETURN_URL", "https://ads.ggcss.xyz/api/ai/receive_ai_ads")
    get_settings.cache_clear()

    service = AdGenerationService()
    job = AdGenerationJob(metadata_json={"return_url": "https://publishing.example/ai-return"})

    assert service.return_url_for_job(job) == "https://publishing.example/ai-return"


def test_ai_ads_access_token_protects_api_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "test-api-token")
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        health_response = client.get("/api/v1/health/live")
        missing_token_response = client.get(
            "/api/v1/integrations/publishing/ad-generation/jobs/job-123/result"
        )
        wrong_token_response = client.get(
            "/api/v1/integrations/publishing/ad-generation/jobs/job-123/result",
            headers={"Authorization": "Bearer wrong-token"},
        )

    assert health_response.status_code == 200
    assert missing_token_response.status_code == 401
    assert wrong_token_response.status_code == 401


@pytest.mark.asyncio
async def test_publishing_ad_generation_result_endpoint_returns_final_json(
    tmp_path,
) -> None:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'adgen-result-returned.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-result-returned",
                return_url="https://publishing.example/ai-return",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: returned result\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: purchase\n"
                        "Landing: https://example.com/returned"
                    ),
                    structured_fields={
                        "project_name": "Returned Result",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "purchase",
                        "landing_url": "https://example.com/returned",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )
        generated = await service.process_job(session, job.id)
        final_payload = {
            **generated.result_payload,
            "status": "final_review",
            "creative_payload": {
                "name": "Returned Result - video",
                "type": "video",
                "message": "Human reviewed primary text",
                "link": "https://example.com/returned",
                "ads_name": "Human reviewed headline",
                "description": "Human reviewed description",
                "asset_url": "https://cdn.example/video.mp4",
                "asset_id": None,
                "image_asset_url": "https://cdn.example/image.png",
                "video_asset_url": "https://cdn.example/video.mp4",
                "draft": 1,
            },
        }
        returned = await service.confirm_review(
            session,
            generated.id,
            PublishingAdGenerationReviewConfirm(
                result_payload=final_payload,
                review_notes="Ready for publishing system.",
            ),
        )

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/integrations/publishing/ad-generation/jobs/{returned.id}/result"
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    result = response.json()
    assert result["job_id"] == returned.id
    assert result["external_order_id"] == "order-result-returned"
    assert result["status"] == "returned"
    assert set(result.keys()) >= {
        "job_id",
        "external_order_id",
        "status",
        "campaign_payload",
        "adset_payload",
        "creative_payload",
        "assets",
        "review",
        "metadata_json",
    }
    assert result["campaign_payload"]["objective"] == "OUTCOME_SALES"
    assert result["creative_payload"]["video_asset_url"] == "https://cdn.example/video.mp4"
    assert "creative_strategy" in result["metadata_json"]
    assert "request_payload" not in result

    await engine.dispose()


@pytest.mark.asyncio
async def test_ai_ads_access_token_accepts_query_and_bearer_tokens(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", "test-api-token")
    get_settings.cache_clear()

    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'adgen-token.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-token",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: token result\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: purchase\n"
                        "Landing: https://example.com/token"
                    ),
                    structured_fields={
                        "project_name": "Token Result",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "purchase",
                        "landing_url": "https://example.com/token",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            query_response = client.get(
                f"/api/v1/integrations/publishing/ad-generation/jobs/{job.id}/result"
                "?access_token=test-api-token"
            )
            ai_query_response = client.get(
                f"/api/v1/integrations/publishing/ad-generation/jobs/{job.id}/result"
                "?ai_access_token=test-api-token"
            )
            bearer_response = client.get(
                f"/api/v1/integrations/publishing/ad-generation/jobs/{job.id}/result",
                headers={"Authorization": "Bearer test-api-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert query_response.status_code == 409
    assert ai_query_response.status_code == 409
    assert bearer_response.status_code == 409

    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_delete_job_removes_it_from_list() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-delete",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: delete flow\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: purchase\n"
                        "Landing: https://example.com/delete"
                    ),
                    structured_fields={
                        "project_name": "Delete Flow",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "purchase",
                        "landing_url": "https://example.com/delete",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )

        await service.delete_job(session, job.id)
        deleted = await session.get(AdGenerationJob, job.id)

    assert deleted is None

    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_delete_job_removes_generated_records_and_files(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()

    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'adgen-delete-cascade.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-delete-cascade",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: delete cascade\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: purchase\n"
                        "Landing: https://example.com/delete-cascade"
                    ),
                    structured_fields={
                        "project_name": "Delete Cascade",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "purchase",
                        "landing_url": "https://example.com/delete-cascade",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )
        generated = await service.process_job(session, job.id)
        campaign_id = generated.result_payload["metadata_json"]["campaign_id"]
        work_order_id = generated.result_payload["metadata_json"]["work_order_id"]
        generated.result_payload = {}

        topic = ContentTopic(
            campaign_id=campaign_id,
            title="Delete topic",
            angle="Delete angle",
        )
        session.add(topic)
        await session.flush()

        draft = CopyDraft(
            campaign_id=campaign_id,
            topic_id=topic.id,
            body="Delete body",
        )
        session.add(draft)
        await session.flush()

        image_path = tmp_path / "images" / campaign_id / "image-1.png"
        image_path.parent.mkdir(parents=True)
        image_path.write_bytes(b"image-bytes")
        creative = CreativeAsset(
            campaign_id=campaign_id,
            draft_id=draft.id,
            url=f"http://api.test/storage/images/{campaign_id}/image-1.png",
            storage_key=f"local://images/{campaign_id}/image-1.png",
            prompt="Delete image",
        )
        session.add(creative)
        await session.flush()

        video_path = tmp_path / "videos" / "video-1" / "provider.mp4"
        video_path.parent.mkdir(parents=True)
        video_path.write_bytes(b"video-bytes")
        video = VideoAsset(
            campaign_id=campaign_id,
            draft_id=draft.id,
            source_asset_ids=[creative.id],
            url="http://api.test/storage/videos/video-1/provider.mp4",
            storage_key="local://videos/video-1/provider.mp4",
            prompt="Delete video",
        )
        session.add(video)
        await session.flush()

        review = ReviewTask(
            campaign_id=campaign_id,
            entity_type="creative_asset",
            entity_id=creative.id,
        )
        agent_run = AgentRun(
            campaign_id=campaign_id,
            graph_name="delete-cascade",
        )
        session.add_all([review, agent_run])
        await session.commit()

        assert image_path.exists()
        assert video_path.exists()

        await service.delete_job(session, generated.id)

    async with session_factory() as session:
        assert await session.get(AdGenerationJob, generated.id) is None
        assert await session.get(Campaign, campaign_id) is None
        assert await session.get(WorkOrder, work_order_id) is None
        assert await session.get(ContentTopic, topic.id) is None
        assert await session.get(CopyDraft, draft.id) is None
        assert await session.get(CreativeAsset, creative.id) is None
        assert await session.get(VideoAsset, video.id) is None
        assert await session.scalar(select(ReviewTask.id).where(ReviewTask.id == review.id)) is None
        assert await session.scalar(select(AgentRun.id).where(AgentRun.id == agent_run.id)) is None

    assert not image_path.exists()
    assert not video_path.exists()

    get_settings.cache_clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_keeps_event_without_pixel_dependency() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-2",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: lead form\n"
                        "Country: Philippines\n"
                        "Audience: age 18-35\n"
                        "Event: lead\n"
                        "Landing: https://example.com/lead"
                    ),
                    structured_fields={
                        "project_name": "Lead Form",
                        "country": "PH",
                        "age_min": 18,
                        "age_max": 35,
                        "event_name": "lead",
                        "landing_url": "https://example.com/lead",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )

        completed = await service.process_job(session, job.id)

    adset_payload = completed.result_payload["adset_payload"]

    assert completed.status == "fields_review"
    assert adset_payload["countries"] == "PH"
    assert adset_payload["event_name"] == "lead"
    assert "pixel_id" not in adset_payload

    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_returns_custom_event_type_for_registration() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-registration",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: registration flow\n"
                        "Country: US\n"
                        "Audience: age 18-35\n"
                        "Optimization event: fast registration\n"
                        "Landing: https://example.com/register"
                    ),
                    structured_fields={
                        "project_name": "Registration Flow",
                        "country": "US",
                        "age_min": 18,
                        "age_max": 35,
                        "customEventType": "\u5feb\u901f\u6ce8\u518c",
                        "landing_url": "https://example.com/register",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )

        completed = await service.process_job(session, job.id)

    campaign_payload = completed.result_payload["campaign_payload"]
    adset_payload = completed.result_payload["adset_payload"]

    assert completed.status == "fields_review"
    assert campaign_payload["objective"] == "OUTCOME_LEADS"
    assert adset_payload["optimization_goal"] == "OFFSITE_CONVERSIONS"
    assert adset_payload["customEventType"] == "COMPLETE_REGISTRATION"
    assert adset_payload["event_name"] == "complete_registration"
    assert "custom_event_type" not in adset_payload

    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_maps_shopping_to_sales_defaults() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-shopping",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: India TV\n"
                        "Country: India\n"
                        "Audience: male age 25-45\n"
                        "Event: shopping\n"
                        "Landing: https://example.com/tv"
                    ),
                    structured_fields={
                        "project_name": "India TV",
                        "country": "\u5370\u5ea6",
                        "age_min": 25,
                        "age_max": 45,
                        "gender": "\u7537",
                        "event_name": "\u8d2d\u7269",
                        "landing_url": "https://example.com/tv",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )

        completed = await service.process_job(session, job.id)

    campaign_payload = completed.result_payload["campaign_payload"]
    adset_payload = completed.result_payload["adset_payload"]

    assert campaign_payload["objective"] == "OUTCOME_SALES"
    assert adset_payload["countries"] == "IN"
    assert adset_payload["optimization_goal"] == "OFFSITE_CONVERSIONS"
    assert adset_payload["billing_event"] == "IMPRESSIONS"
    assert adset_payload["bid_strategy"] == "LOWEST_COST_WITHOUT_CAP"
    assert adset_payload["start_type"] == "I"
    assert adset_payload["start_time"] == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_reuses_provided_delivery_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()

        async def fail_if_called(raw_content: str):  # pragma: no cover - should never run
            raise AssertionError("delivery extraction should be reused instead of called again")

        monkeypatch.setattr(service.work_orders, "extract_delivery_fields", fail_if_called)

        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-reuse-extraction",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: India TV\n"
                        "Country: India\n"
                        "Audience: male age 25-45\n"
                        "Event: shopping\n"
                        "Landing: https://example.com/tv"
                    ),
                    structured_fields={
                        "project_name": "India TV",
                        "country": "IN",
                        "age_min": 25,
                        "age_max": 45,
                        "gender": "male",
                        "event_name": "shopping",
                        "landing_url": "https://example.com/tv",
                    },
                    delivery_extraction=_delivery_extraction(),
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )

        completed = await service.process_job(session, job.id)

    result = completed.result_payload
    campaign_payload = result["campaign_payload"]
    adset_payload = result["adset_payload"]

    assert completed.status == "fields_review"
    assert campaign_payload["objective"] == "OUTCOME_SALES"
    assert adset_payload["countries"] == "IN"
    assert adset_payload["age_min"] == 25
    assert adset_payload["age_max"] == 45
    assert result["metadata_json"]["extraction_source"] == "provided"

    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_review_confirm_marks_job_reviewed() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-3",
                return_url="https://publishing.example/ai-return",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: review flow\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: purchase\n"
                        "Landing: https://example.com/review"
                    ),
                    structured_fields={
                        "project_name": "Review Flow",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "purchase",
                        "landing_url": "https://example.com/review",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )
        generated = await service.process_job(session, job.id)
        edited_payload = {
            **generated.result_payload,
            "status": "final_review",
            "creative_payload": {
                "name": "Review Flow - video",
                "type": "video",
                "message": "Human reviewed primary text",
                "link": "https://example.com/review",
                "ads_name": "Human reviewed headline",
                "description": "Human reviewed description",
                "asset_url": "https://cdn.example/video.mp4",
                "asset_id": None,
                "draft": 1,
            },
        }

        reviewed = await service.confirm_review(
            session,
            generated.id,
            PublishingAdGenerationReviewConfirm(
                result_payload=edited_payload,
                review_notes="Looks good.",
            ),
        )

    assert reviewed.status == "returned"
    assert reviewed.result_payload["status"] == "returned"
    assert reviewed.result_payload["creative_payload"]["ads_name"] == "Human reviewed headline"
    assert reviewed.metadata_json["return_url"] == "https://publishing.example/ai-return"

    await engine.dispose()


@pytest.mark.asyncio
async def test_confirm_review_returns_without_removed_review_gate_fields() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-direct-return",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: direct return\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: traffic\n"
                        "Landing: https://example.com/direct-return"
                    ),
                    structured_fields={
                        "project_name": "Direct Return",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "traffic",
                        "landing_url": "https://example.com/direct-return",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )
        generated = await service.process_job(session, job.id)
        reviewed_payload = {
            **generated.result_payload,
            "status": "final_review",
            "creative_payload": {
                "name": "Direct Return - image",
                "type": "image",
                "message": "Free deal today with cash and chips.",
                "link": "https://example.com/direct-return",
                "ads_name": "Operator approved headline",
                "description": "Operator approved description",
                "asset_url": "https://cdn.example/image.png",
                "image_asset_url": "https://cdn.example/image.png",
                "draft": 1,
            },
        }

        returned = await service.confirm_review(
            session,
            generated.id,
            PublishingAdGenerationReviewConfirm(result_payload=reviewed_payload),
        )

    assert returned.status == "returned"
    assert returned.result_payload["status"] == "returned"
    assert "brand_safety" not in returned.result_payload.get("review", {})
    assert "brand_safety_mode" not in returned.metadata_json
    assert "brand_safety_status" not in returned.metadata_json

    await engine.dispose()

@pytest.mark.asyncio
async def test_publishing_ad_generation_confirm_queues_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ai.example.test")
    get_settings.cache_clear()

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    captured: dict = {}
    confirm_completed = False

    async def fake_post_callback(
        self: AdGenerationService,
        callback_url: str,
        payload: dict,
    ) -> dict:
        assert confirm_completed, "callback should run from callback_queue after confirm returns"
        captured["callback_url"] = callback_url
        captured["payload"] = payload
        return {
            "url": callback_url,
            "status": "succeeded",
            "status_code": 204,
            "request_payload": payload,
            "response_text": "",
            "started_at": "2026-06-18T00:00:00+00:00",
            "finished_at": "2026-06-18T00:00:01+00:00",
        }

    monkeypatch.setattr(AdGenerationService, "_post_callback", fake_post_callback)

    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                external_order_id="order-callback",
                callback_url="https://publishing.example/api/ai-callback",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: callback flow\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: purchase\n"
                        "Landing: https://example.com/callback"
                    ),
                    structured_fields={
                        "project_name": "Callback Flow",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "purchase",
                        "landing_url": "https://example.com/callback",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )
        generated = await service.process_job(session, job.id)

        returned = await service.confirm_review(
            session,
            generated.id,
            PublishingAdGenerationReviewConfirm(
                result_payload=generated.result_payload,
                review_notes="Callback ready.",
            ),
        )

    assert returned.status == "returned"
    assert captured == {}
    callback_delivery = returned.metadata_json["callback_delivery"]
    assert callback_delivery["status"] == "queued"
    assert callback_delivery["queue_name"] == "callback_queue"
    assert callback_delivery["task_id"]

    async with session_factory() as session:
        queued_task = await session.get(GenerationTask, callback_delivery["task_id"])

    assert queued_task is not None
    assert queued_task.status == "queued"
    assert queued_task.queue_name == "callback_queue"
    assert queued_task.task_type == "ad_generation_callback"
    assert queued_task.business_type == "ad_generation_job"
    assert queued_task.business_id == returned.id
    assert queued_task.max_attempts == 3

    confirm_completed = True
    await GenerationTaskService().process_task(queued_task.id)

    async with session_factory() as session:
        processed_task = await session.get(GenerationTask, queued_task.id)
        processed_job = await session.get(AdGenerationJob, returned.id)

    assert processed_task is not None
    assert processed_task.status == "succeeded"
    assert processed_task.result_json is not None
    assert processed_task.result_json["status"] == "succeeded"
    assert captured["callback_url"] == "https://publishing.example/api/ai-callback"
    assert captured["payload"]["event"] == "ad_generation.returned"
    assert captured["payload"]["job_id"] == returned.id
    assert captured["payload"]["external_order_id"] == "order-callback"
    assert captured["payload"]["status"] == "returned"
    assert (
        captured["payload"]["result_url"]
        == f"https://ai.example.test/api/v1/integrations/publishing/ad-generation/jobs/{returned.id}/result"
    )
    assert processed_job is not None
    callback_delivery = processed_job.metadata_json["callback_delivery"]
    assert callback_delivery["status"] == "succeeded"
    assert callback_delivery["attempts"] == 1
    assert callback_delivery["task_id"] == queued_task.id
    assert callback_delivery["queue_name"] == "callback_queue"
    assert callback_delivery["last_status_code"] == 204
    assert callback_delivery["last_result"]["request_payload"] == captured["payload"]

    get_settings.cache_clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_update_cannot_roll_back_returned_status() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: returned rollback guard\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: purchase\n"
                        "Landing: https://example.com/rollback"
                    ),
                    structured_fields={
                        "project_name": "Returned Rollback Guard",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "purchase",
                        "landing_url": "https://example.com/rollback",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )
        generated = await service.process_job(session, job.id)
        returned = await service.confirm_review(
            session,
            generated.id,
            PublishingAdGenerationReviewConfirm(result_payload=generated.result_payload),
        )

        updated = await service.update_review_payload(
            session,
            returned.id,
            PublishingAdGenerationReviewUpdate(
                result_payload={**returned.result_payload, "status": "final_review"}
            ),
        )

    assert updated.status == "returned"
    assert updated.result_payload["status"] == "returned"
    assert updated.completed_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_callback_failure_does_not_block_returned_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)

    async def fake_post_callback(
        self: AdGenerationService,
        callback_url: str,
        payload: dict,
    ) -> dict:
        return {
            "url": callback_url,
            "status": "failed",
            "status_code": 500,
            "request_payload": payload,
            "response_text": "temporary failure",
            "error": "Callback endpoint returned HTTP 500.",
            "started_at": "2026-06-18T00:00:00+00:00",
            "finished_at": "2026-06-18T00:00:01+00:00",
        }

    monkeypatch.setattr(AdGenerationService, "_post_callback", fake_post_callback)

    async with session_factory() as session:
        service = AdGenerationService()
        job = await service.create_job(
            session,
            PublishingAdGenerationJobCreate(
                callback_url="https://publishing.example/api/ai-callback",
                work_order=PublishingWorkOrderPayload(
                    raw_content=(
                        "Project: failed callback flow\n"
                        "Country: US\n"
                        "Audience: age 25-45\n"
                        "Event: purchase\n"
                        "Landing: https://example.com/callback-failure"
                    ),
                    structured_fields={
                        "project_name": "Failed Callback Flow",
                        "country": "US",
                        "age_min": 25,
                        "age_max": 45,
                        "event_name": "purchase",
                        "landing_url": "https://example.com/callback-failure",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )
        generated = await service.process_job(session, job.id)

        returned = await service.confirm_review(
            session,
            generated.id,
            PublishingAdGenerationReviewConfirm(result_payload=generated.result_payload),
        )

    assert returned.status == "returned"
    assert returned.result_payload["status"] == "returned"
    queued_delivery = returned.metadata_json["callback_delivery"]
    assert queued_delivery["status"] == "queued"

    await GenerationTaskService().process_task(queued_delivery["task_id"])

    async with session_factory() as session:
        task = await session.get(GenerationTask, queued_delivery["task_id"])
        processed_job = await session.get(AdGenerationJob, returned.id)

    assert task is not None
    assert task.status == "failed"
    assert task.retryable is True
    assert task.error_code == "unknown_provider_error"
    assert task.error_message == "Callback endpoint returned HTTP 500."

    assert processed_job is not None
    assert processed_job.status == "returned"
    callback_delivery = processed_job.metadata_json["callback_delivery"]
    assert callback_delivery["status"] == "failed"
    assert callback_delivery["attempts"] == 1
    assert callback_delivery["task_id"] == queued_delivery["task_id"]
    assert callback_delivery["queue_name"] == "callback_queue"
    assert callback_delivery["last_status_code"] == 500
    assert callback_delivery["last_result"]["error"] == "Callback endpoint returned HTTP 500."

    await engine.dispose()
