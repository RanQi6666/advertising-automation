import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.schemas.ad_generation import (
    PublishingAdGenerationJobCreate,
    PublishingAdGenerationPreferences,
    PublishingWorkOrderPayload,
)
from backend.app.services.ad_generation_service import AdGenerationService


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

    assert completed.status == "completed"
    assert campaign_payload["objective"] == "OUTCOME_SALES"
    assert campaign_payload["draft"] == 1
    assert adset_payload["countries"] == "US"
    assert isinstance(adset_payload["countries"], str)
    assert adset_payload["country_code"] == "US"
    assert adset_payload["age_min"] == 25
    assert adset_payload["age_max"] == 45
    assert adset_payload["optimization_goal"] == "LINK_CLICKS"
    assert adset_payload["custom_event_type"] is None
    assert creative_payload["link"] == "https://example.com/product"
    assert creative_payload["message"]
    assert creative_payload["ads_name"]
    assert creative_payload["asset_id"] is None
    assert creative_payload["asset_url"] is None
    assert "btn_type" not in creative_payload
    assert "page_id" not in creative_payload
    assert result["assets"]["images"][0]["url"] is None
    assert any("public asset URL" in warning for warning in result["review"]["warnings"])

    await engine.dispose()


@pytest.mark.asyncio
async def test_publishing_ad_generation_uses_pixel_for_conversion_suggestion() -> None:
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
                        "pixel_id": "pixel-123",
                    },
                ),
                preferences=PublishingAdGenerationPreferences(image_count=1),
            ),
        )

        completed = await service.process_job(session, job.id)

    adset_payload = completed.result_payload["adset_payload"]

    assert completed.status == "completed"
    assert adset_payload["countries"] == "PH"
    assert adset_payload["optimization_goal"] == "OFFSITE_CONVERSIONS"
    assert adset_payload["pixel_id"] == "pixel-123"
    assert adset_payload["custom_event_type"] == "LEAD"

    await engine.dispose()
