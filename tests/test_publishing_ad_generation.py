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
    PublishingWorkOrderPayload,
)
from backend.app.services.ad_generation_service import AdGenerationService


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
    assert result["campaign_payload"]["objective"] == "OUTCOME_SALES"
    assert result["creative_payload"]["video_asset_url"] == "https://cdn.example/video.mp4"
    assert "request_payload" not in result

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
