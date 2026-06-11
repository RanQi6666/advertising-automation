import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import Settings
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.enums import ReviewDecision, ReviewEntityType
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.work_order import WorkOrder
from backend.app.integrations.facebook.graph_client import FacebookGraphClient
from backend.app.schemas.publishing import (
    AdsPlanDraftRequest,
    MetaAdsDraftCreateRequest,
    MetaAdsPackagePrepareRequest,
)
from backend.app.schemas.review import ReviewCreate
from backend.app.services.publish_service import PublishService
from backend.app.services.review_service import ReviewService


@pytest.mark.asyncio
async def test_ads_plan_draft_without_pixel_falls_back_to_traffic_payloads() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        work_order = WorkOrder(
            id="work-order-1",
            raw_content=(
                "项目名称：印度tv8%\n"
                "投放国家：印度\n"
                "投放事件：购物\n"
                "投放人群：男。年龄25-45\n"
                "打款金额：216（广告过审打款）\n"
                "投放链接：https://example.com/tv"
            ),
            parsed_fields={
                "country": "印度",
                "event_name": "购物",
                "audience_description": "男。年龄25-45",
                "payout_amount": "216（广告过审打款）",
                "landing_url": "https://example.com/tv",
            },
            project_name="印度tv8%",
            country="印度",
            media="fb",
            event_name="购物",
            product_name="印度tv",
            audience_description="男。年龄25-45",
            landing_url="https://example.com/tv",
        )
        campaign = Campaign(
            id="campaign-1",
            work_order_id=work_order.id,
            name="印度tv8%",
            objective="购物",
            product_name="印度tv",
            audience_description="男。年龄25-45",
            metadata_json={},
        )
        topic = ContentTopic(
            id="topic-1",
            campaign_id=campaign.id,
            title="Free IPL Live TV",
            angle="Watch live cricket without subscription.",
            source_data={},
        )
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id=topic.id,
            body="Body fallback",
            primary_text="Watch IPL 2025 live for free.",
            headline="Draft headline",
            description="No subscription needed.",
            version=1,
            metadata_json={},
        )
        session.add_all([work_order, campaign, topic, draft])
        await session.commit()

        result = await PublishService().build_ads_plan_draft(
            session,
            AdsPlanDraftRequest(
                campaign_id=campaign.id,
                draft_id=draft.id,
                topic_id=topic.id,
                page_id="page-123",
                ad_account_id="123456",
            ),
        )

    assert result["destination_url"] == "https://example.com/tv"
    assert result["headline"] == "Free IPL Live TV"
    assert result["primary_text"] == "Watch IPL 2025 live for free."
    assert result["campaign_payload"] == {
        "name": "印度tv8%",
        "objective": "OUTCOME_TRAFFIC",
        "status": "PAUSED",
        "special_ad_categories": [],
        "is_adset_budget_sharing_enabled": False,
    }
    assert result["adset_payload"]["optimization_goal"] == "LINK_CLICKS"
    assert result["adset_payload"]["targeting"] == {
        "geo_locations": {"countries": ["IN"]},
        "age_min": 25,
        "age_max": 45,
        "genders": [1],
        "targeting_automation": {"advantage_audience": 0},
    }
    assert "promoted_object" not in result["adset_payload"]
    assert result["adset_payload"]["daily_budget"] == "<DAILY_BUDGET_REQUIRED>"
    assert result["ad_payload"]["status"] == "PAUSED"
    assert result["meta_payload"]["campaign"]["endpoint"].endswith("/act_123456/campaigns")
    assert result["meta_payload"]["adset"]["endpoint"].endswith("/act_123456/adsets")
    assert result["meta_payload"]["ad"]["endpoint"].endswith("/act_123456/ads")
    assert result["targeting_summary"]["country_code"] == "IN"
    assert result["targeting_summary"]["gender_label"] == "male"
    assert result["targeting_summary"]["mapped_objective"] == "OUTCOME_TRAFFIC"
    assert result["targeting_summary"]["optimization_goal"] == "LINK_CLICKS"
    assert "adset.daily_budget" in result["source_mapping"]
    assert any("Pixel ID" in warning for warning in result["warnings"])

    await engine.dispose()


@pytest.mark.asyncio
async def test_create_meta_ads_draft_creates_paused_dry_run_job() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        work_order = WorkOrder(
            id="work-order-1",
            raw_content="投放国家：印度\n投放事件：购物\n投放人群：男。年龄25-45",
            parsed_fields={
                "country": "印度",
                "event_name": "购物",
                "audience_description": "男。年龄25-45",
                "landing_url": "https://example.com/tv",
            },
            country="印度",
            event_name="购物",
            audience_description="男。年龄25-45",
            landing_url="https://example.com/tv",
        )
        campaign = Campaign(
            id="campaign-1",
            work_order_id=work_order.id,
            name="India TV",
            objective="购物",
            product_name="India TV",
            audience_description="男。年龄25-45",
            metadata_json={},
        )
        topic = ContentTopic(
            id="topic-1",
            campaign_id=campaign.id,
            title="Free IPL Live TV",
            angle="Watch live cricket without subscription.",
            source_data={},
        )
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id=topic.id,
            body="Body fallback",
            primary_text="Watch IPL live for free.",
            headline="Draft headline",
            description="No subscription needed.",
            version=1,
            metadata_json={},
        )
        session.add_all([work_order, campaign, topic, draft])
        await session.commit()

        service = PublishService()
        service.settings = Settings(
            facebook_dry_run=True,
            facebook_page_id="page-123",
            facebook_page_access_token="page-token",
            facebook_ad_account_id="act_123456",
            facebook_ad_access_token="ad-token",
        )
        service.facebook = FacebookGraphClient(service.settings)

        result = await service.create_meta_ads_draft(
            session,
            MetaAdsDraftCreateRequest(
                campaign_id=campaign.id,
                draft_id=draft.id,
                topic_id=topic.id,
                daily_budget=100,
                pixel_id="pixel-123",
                confirm_create_paused=True,
            ),
        )

    assert result["status"] == "published"
    assert result["dry_run"] is True
    assert result["meta_campaign_id"].startswith("dry_run_campaign_")
    assert result["meta_adset_id"].startswith("dry_run_adset_")
    assert result["meta_ad_creative_id"].startswith("dry_run_adcreative_")
    assert result["meta_ad_id"].startswith("dry_run_ad_")
    assert result["plan"]["campaign_payload"]["status"] == "PAUSED"
    assert result["plan"]["campaign_payload"]["objective"] == "OUTCOME_SALES"
    assert result["plan"]["adset_payload"]["daily_budget"] == 100
    assert result["plan"]["adset_payload"]["optimization_goal"] == "OFFSITE_CONVERSIONS"
    assert result["plan"]["adset_payload"]["promoted_object"]["pixel_id"] == "pixel-123"

    await engine.dispose()


@pytest.mark.asyncio
async def test_meta_ads_package_requires_review_before_one_click_publish() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        work_order = WorkOrder(
            id="work-order-1",
            raw_content="投放国家：印度\n投放事件：购物\n投放人群：男。年龄25-45",
            parsed_fields={
                "country": "印度",
                "event_name": "购物",
                "audience_description": "男。年龄25-45",
                "landing_url": "https://example.com/tv",
            },
            country="印度",
            event_name="购物",
            audience_description="男。年龄25-45",
            landing_url="https://example.com/tv",
        )
        campaign = Campaign(
            id="campaign-1",
            work_order_id=work_order.id,
            name="India TV",
            objective="购物",
            product_name="India TV",
            audience_description="男。年龄25-45",
            metadata_json={},
        )
        topic = ContentTopic(
            id="topic-1",
            campaign_id=campaign.id,
            title="Free IPL Live TV",
            angle="Watch live cricket without subscription.",
            source_data={},
        )
        draft = CopyDraft(
            id="draft-1",
            campaign_id=campaign.id,
            topic_id=topic.id,
            body="Body fallback",
            primary_text="Watch IPL live for free.",
            headline="Draft headline",
            description="No subscription needed.",
            version=1,
            metadata_json={},
        )
        session.add_all([work_order, campaign, topic, draft])
        await session.commit()

        service = PublishService()
        service.settings = Settings(
            facebook_dry_run=True,
            facebook_page_id="page-123",
            facebook_page_access_token="page-token",
            facebook_ad_account_id="act_123456",
            facebook_ad_access_token="ad-token",
        )
        service.facebook = FacebookGraphClient(service.settings)

        job = await service.prepare_meta_ads_package(
            session,
            MetaAdsPackagePrepareRequest(
                campaign_id=campaign.id,
                draft_id=draft.id,
                topic_id=topic.id,
                daily_budget=100,
                confirm_prepare=True,
            ),
        )

        assert job.status == "queued"
        assert job.payload["ad_operation"] == "create_meta_ads_draft"
        assert job.payload["plan"]["campaign_payload"]["objective"] == "OUTCOME_TRAFFIC"
        assert job.payload["plan"]["adset_payload"]["optimization_goal"] == "LINK_CLICKS"
        assert "promoted_object" not in job.payload["plan"]["adset_payload"]
        assert job.metadata_json["review_status"] == "pending"

        unapproved = await service.publish_job(session, job.id)
        assert unapproved.status == "failed"
        assert "approved" in str(unapproved.error_message)

        await ReviewService().submit_review(
            session,
            ReviewCreate(
                entity_type=ReviewEntityType.PUBLISH_JOB,
                entity_id=job.id,
                campaign_id=campaign.id,
                decision=ReviewDecision.APPROVED,
            ),
        )

        published = await service.publish_job(session, job.id)

    assert published.status == "published"
    assert published.external_id.startswith("dry_run_ad_")
    assert published.metadata_json["review_status"] == "approved"
    assert published.metadata_json["provider_response"]["meta_ads_ids"]["ad_id"].startswith(
        "dry_run_ad_"
    )

    await engine.dispose()
