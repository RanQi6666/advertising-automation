import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import Settings
from backend.app.core.errors import ProviderError
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import (
    PublishChannel,
    PublishStatus,
    ReviewDecision,
    ReviewEntityType,
)
from backend.app.db.models.insight import InsightDaily
from backend.app.db.models.publish_job import PublishJob
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.db.models.work_order import WorkOrder
from backend.app.integrations.facebook.graph_client import FacebookGraphClient
from backend.app.schemas.publishing import (
    AdsPlanDraftRequest,
    MetaAdsActivationRequest,
    MetaAdsDraftCreateRequest,
    MetaAdsPackagePrepareRequest,
    MetaAdsPauseRequest,
)
from backend.app.schemas.review import ReviewCreate
from backend.app.services.image_storage_service import ImageStorageService
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
                facebook_image_hash="image-hash-123",
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
async def test_ads_plan_draft_requires_image_or_video_material() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-1",
            name="India TV",
            objective="traffic",
            product_name="India TV",
            audience_description="male 25-45",
            metadata_json={"work_order": {"landing_url": "https://example.com/tv"}},
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
            version=1,
            metadata_json={},
        )
        session.add_all([campaign, topic, draft])
        await session.commit()

        with pytest.raises(ProviderError, match="image or video"):
            await PublishService().build_ads_plan_draft(
                session,
                AdsPlanDraftRequest(
                    campaign_id=campaign.id,
                    draft_id=draft.id,
                    topic_id=topic.id,
                    page_id="page-123",
                    ad_account_id="123456",
                ),
            )

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
            facebook_ads_dry_run=True,
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
                facebook_image_hash="image-hash-123",
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
async def test_meta_ads_package_uploads_local_image_and_uses_image_hash(tmp_path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    local_image = tmp_path / "images" / "campaign-1" / "creative-1.jpeg"
    local_image.parent.mkdir(parents=True)
    local_image.write_bytes(b"fake image bytes")

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
        creative = CreativeAsset(
            id="creative-1",
            campaign_id=campaign.id,
            draft_id=draft.id,
            kind="image",
            url="http://127.0.0.1:8001/storage/images/campaign-1/creative-1.jpeg",
            storage_key="local://images/campaign-1/creative-1.jpeg",
            prompt="Image prompt",
            alt_text="Image alt",
            size="1:1",
            metadata_json={},
        )
        session.add_all([work_order, campaign, topic, draft, creative])
        await session.commit()

        service = PublishService()
        service.settings = Settings(
            facebook_dry_run=True,
            facebook_ads_dry_run=True,
            facebook_page_id="page-123",
            facebook_ad_account_id="act_123456",
            local_storage_root=str(tmp_path),
            public_base_url="http://127.0.0.1:8001",
        )
        service.facebook = FacebookGraphClient(service.settings)
        service.image_storage = ImageStorageService(service.settings)

        job = await service.prepare_meta_ads_package(
            session,
            MetaAdsPackagePrepareRequest(
                campaign_id=campaign.id,
                draft_id=draft.id,
                topic_id=topic.id,
                creative_asset_id=creative.id,
                daily_budget=100,
                confirm_prepare=True,
            ),
        )

    plan = job.payload["plan"]
    link_data = plan["creative_payload"]["object_story_spec"]["link_data"]
    image_hash = link_data["image_hash"]

    assert image_hash.startswith("dry_run_image_hash_")
    assert "picture" not in link_data
    assert plan["facebook_image_hash"] == image_hash
    assert creative.metadata_json["facebook_image_hashes"]["act_123456"] == image_hash
    assert not any("Image is not uploaded" in warning for warning in plan["warnings"])

    await engine.dispose()


@pytest.mark.asyncio
async def test_activate_meta_ads_job_updates_paused_objects_in_order() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-1",
            name="India TV",
            objective="traffic",
            product_name="India TV",
            audience_description="male 25-45",
            metadata_json={},
        )
        job = PublishJob(
            id="publish-job-1",
            campaign_id=campaign.id,
            draft_id=None,
            channel=PublishChannel.FACEBOOK_AD.value,
            payload={
                "ad_operation": "create_meta_ads_draft",
                "facebook_account_id": None,
            },
            status=PublishStatus.PUBLISHED.value,
            external_id="ad-123",
            metadata_json={
                "meta_ads_ids": {
                    "campaign_id": "campaign-123",
                    "adset_id": "adset-123",
                    "ad_creative_id": "creative-123",
                    "ad_id": "ad-123",
                },
                "plan": {},
            },
        )
        session.add_all([campaign, job])
        await session.commit()

        service = PublishService()
        service.settings = Settings(
            facebook_dry_run=True,
            facebook_ads_dry_run=True,
            facebook_ad_access_token="ad-token",
        )
        service.facebook = FacebookGraphClient(service.settings)

        activated = await service.activate_meta_ads_job(
            session,
            job.id,
            MetaAdsActivationRequest(
                confirm_activate=True,
                confirmation_text="ACTIVE",
            ),
        )

    activation = activated.metadata_json["activation"]
    assert activated.status == "published"
    assert activated.metadata_json["delivery_status"] == "active"
    assert activation["status"] == "active"
    assert activation["responses"]["campaign"]["payload"] == {"status": "ACTIVE"}
    assert activation["responses"]["adset"]["payload"] == {"status": "ACTIVE"}
    assert activation["responses"]["ad"]["payload"] == {"status": "ACTIVE"}

    await engine.dispose()


@pytest.mark.asyncio
async def test_pause_meta_ads_job_updates_objects_from_ad_to_campaign() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-1",
            name="India TV",
            objective="traffic",
            product_name="India TV",
            audience_description="male 25-45",
            metadata_json={},
        )
        job = PublishJob(
            id="publish-job-1",
            campaign_id=campaign.id,
            draft_id=None,
            channel=PublishChannel.FACEBOOK_AD.value,
            payload={
                "ad_operation": "create_meta_ads_draft",
                "facebook_account_id": None,
            },
            status=PublishStatus.PUBLISHED.value,
            external_id="ad-123",
            metadata_json={
                "activation": {"status": "active"},
                "delivery_status": "active",
                "meta_ads_ids": {
                    "campaign_id": "campaign-123",
                    "adset_id": "adset-123",
                    "ad_creative_id": "creative-123",
                    "ad_id": "ad-123",
                },
                "plan": {},
            },
        )
        session.add_all([campaign, job])
        await session.commit()

        service = PublishService()
        service.settings = Settings(
            facebook_dry_run=True,
            facebook_ads_dry_run=True,
            facebook_ad_access_token="ad-token",
        )
        service.facebook = FacebookGraphClient(service.settings)

        paused = await service.pause_meta_ads_job(
            session,
            job.id,
            MetaAdsPauseRequest(
                confirm_pause=True,
                confirmation_text="PAUSE",
            ),
        )

    pause = paused.metadata_json["pause"]
    assert paused.status == "published"
    assert paused.metadata_json["delivery_status"] == "paused"
    assert paused.metadata_json["activation"]["status"] == "paused"
    assert pause["status"] == "paused"
    assert list(pause["responses"].keys()) == ["ad", "adset", "campaign"]
    assert pause["responses"]["ad"]["payload"] == {"status": "PAUSED"}
    assert pause["responses"]["adset"]["payload"] == {"status": "PAUSED"}
    assert pause["responses"]["campaign"]["payload"] == {"status": "PAUSED"}

    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_meta_ads_status_writes_review_summary() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    class FakeFacebookGraphClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def get_ad_object_status(
            self,
            object_id: str,
            access_token_ref: str | None,
            fields: list[str] | None = None,
        ) -> dict:
            self.calls.append(object_id)
            effective_status = "PENDING_REVIEW" if object_id == "ad-123" else "ACTIVE"
            return {
                "id": object_id,
                "name": object_id,
                "status": "ACTIVE",
                "configured_status": "ACTIVE",
                "effective_status": effective_status,
                "access_token_ref": access_token_ref,
                "fields": fields,
            }

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-1",
            name="India TV",
            objective="traffic",
            product_name="India TV",
            audience_description="male 25-45",
            metadata_json={},
        )
        job = PublishJob(
            id="publish-job-1",
            campaign_id=campaign.id,
            draft_id=None,
            channel=PublishChannel.FACEBOOK_AD.value,
            payload={
                "ad_operation": "create_meta_ads_draft",
                "facebook_account_id": None,
            },
            status=PublishStatus.PUBLISHED.value,
            external_id="ad-123",
            metadata_json={
                "meta_ads_ids": {
                    "campaign_id": "campaign-123",
                    "adset_id": "adset-123",
                    "ad_id": "ad-123",
                },
                "plan": {},
            },
        )
        session.add_all([campaign, job])
        await session.commit()

        fake_facebook = FakeFacebookGraphClient()
        service = PublishService()
        service.settings = Settings(
            facebook_dry_run=True,
            facebook_ads_dry_run=True,
            facebook_ad_access_token="ad-token",
        )
        service.facebook = fake_facebook

        synced = await service.sync_meta_ads_status_job(session, job.id)

    assert fake_facebook.calls == ["campaign-123", "adset-123", "ad-123"]
    assert synced.metadata_json["meta_review_status"] == "reviewing"
    assert synced.metadata_json["delivery_status"] == "meta_reviewing"
    assert synced.metadata_json["meta_status"]["summary"]["review_status"] == "reviewing"
    ad_status = synced.metadata_json["meta_status"]["objects"]["ad"]
    assert ad_status["effective_status"] == "PENDING_REVIEW"

    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_meta_ads_insights_writes_summary_and_daily_snapshot() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    class FakeFacebookGraphClient:
        async def get_ad_object_status(
            self,
            object_id: str,
            access_token_ref: str | None,
            fields: list[str] | None = None,
        ) -> dict:
            common = {
                "id": object_id,
                "status": "ACTIVE",
                "configured_status": "ACTIVE",
                "effective_status": "ACTIVE",
                "updated_time": "2026-06-13T10:00:00+0800",
                "access_token_ref": access_token_ref,
                "fields": fields,
            }
            if object_id == "campaign-123":
                return {**common, "name": "Campaign", "bid_strategy": "LOWEST_COST_WITHOUT_CAP"}
            if object_id == "adset-123":
                return {
                    **common,
                    "name": "Ad Set",
                    "daily_budget": "500",
                    "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
                    "optimization_goal": "LINK_CLICKS",
                    "billing_event": "IMPRESSIONS",
                    "end_time": "2026-06-30T00:00:00+0800",
                    "attribution_spec": [{"event_type": "CLICK_THROUGH", "window_days": 7}],
                }
            return {**common, "name": "Ad"}

        async def get_ad_insights(
            self,
            ad_id: str,
            access_token_ref: str | None,
            fields: list[str] | None = None,
            date_preset: str = "today",
        ) -> dict:
            return {
                "data": [
                    {
                        "ad_id": ad_id,
                        "ad_name": "Ad",
                        "adset_id": "adset-123",
                        "adset_name": "Ad Set",
                        "campaign_id": "campaign-123",
                        "campaign_name": "Campaign",
                        "spend": "12.34",
                        "impressions": "1000",
                        "reach": "800",
                        "clicks": "45",
                        "inline_link_clicks": "40",
                        "actions": [{"action_type": "link_click", "value": "40"}],
                        "cost_per_action_type": [
                            {"action_type": "link_click", "value": "0.31"}
                        ],
                        "quality_ranking": "AVERAGE",
                        "engagement_rate_ranking": "ABOVE_AVERAGE",
                        "conversion_rate_ranking": "UNKNOWN",
                        "cpc": "0.27",
                        "ctr": "4.5",
                        "account_currency": "USD",
                    }
                ],
                "date_preset": date_preset,
                "access_token_ref": access_token_ref,
                "fields": fields,
            }

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        campaign = Campaign(
            id="campaign-1",
            name="India TV",
            objective="traffic",
            product_name="India TV",
            audience_description="male 25-45",
            metadata_json={},
        )
        job = PublishJob(
            id="publish-job-1",
            campaign_id=campaign.id,
            draft_id=None,
            channel=PublishChannel.FACEBOOK_AD.value,
            payload={
                "ad_operation": "create_meta_ads_draft",
                "facebook_account_id": None,
            },
            status=PublishStatus.PUBLISHED.value,
            external_id="ad-123",
            metadata_json={
                "meta_ads_ids": {
                    "campaign_id": "campaign-123",
                    "adset_id": "adset-123",
                    "ad_id": "ad-123",
                },
                "plan": {
                    "targeting_summary": {
                        "conversion_event_type": "LINK_CLICK",
                    },
                },
            },
        )
        session.add_all([campaign, job])
        await session.commit()

        service = PublishService()
        service.settings = Settings(
            facebook_dry_run=True,
            facebook_ads_dry_run=True,
            facebook_ad_access_token="ad-token",
        )
        service.facebook = FakeFacebookGraphClient()

        synced = await service.sync_meta_ads_insights_job(session, job.id)
        insight_rows = list((await session.execute(select(InsightDaily))).scalars().all())

    summary = synced.metadata_json["meta_insights"]["summary"]
    assert summary["spend"] == "12.34"
    assert summary["impressions"] == 1000
    assert summary["reach"] == 800
    assert summary["result_count"] == 40
    assert summary["cost_per_result"] == "0.31"
    assert summary["budget_type"] == "daily_budget"
    assert summary["budget_amount"] == "500"
    assert summary["quality_ranking"] == "AVERAGE"
    assert synced.metadata_json["delivery_status"] == "meta_active"
    assert len(insight_rows) == 1
    assert insight_rows[0].publish_job_id == job.id
    assert str(insight_rows[0].spend) == "12.34"
    assert insight_rows[0].impressions == 1000
    assert insight_rows[0].clicks == 40

    await engine.dispose()


@pytest.mark.asyncio
async def test_meta_ads_package_adds_image_hash_thumbnail_for_video(tmp_path) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    local_image = tmp_path / "images" / "campaign-1" / "creative-1.jpeg"
    local_image.parent.mkdir(parents=True)
    local_image.write_bytes(b"fake image bytes")

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
        creative = CreativeAsset(
            id="creative-1",
            campaign_id=campaign.id,
            draft_id=draft.id,
            kind="image",
            url="http://127.0.0.1:8001/storage/images/campaign-1/creative-1.jpeg",
            storage_key="local://images/campaign-1/creative-1.jpeg",
            prompt="Image prompt",
            alt_text="Image alt",
            size="1:1",
            metadata_json={},
        )
        video = VideoAsset(
            id="video-1",
            campaign_id=campaign.id,
            draft_id=draft.id,
            source_asset_ids=[creative.id],
            url="http://127.0.0.1:8001/storage/videos/video-1/video.mp4",
            storage_key="local://videos/video-1/video.mp4",
            prompt="Video prompt",
            storyboard=[],
            duration_seconds=6,
            aspect_ratio="9:16",
            status="approved",
            metadata_json={"facebook_video_id": "video-123"},
        )
        session.add_all([work_order, campaign, topic, draft, creative, video])
        await session.commit()

        service = PublishService()
        service.settings = Settings(
            facebook_dry_run=True,
            facebook_ads_dry_run=True,
            facebook_page_id="page-123",
            facebook_ad_account_id="act_123456",
            local_storage_root=str(tmp_path),
            public_base_url="http://127.0.0.1:8001",
        )
        service.facebook = FacebookGraphClient(service.settings)
        service.image_storage = ImageStorageService(service.settings)

        job = await service.prepare_meta_ads_package(
            session,
            MetaAdsPackagePrepareRequest(
                campaign_id=campaign.id,
                draft_id=draft.id,
                topic_id=topic.id,
                video_asset_id=video.id,
                daily_budget=100,
                confirm_prepare=True,
            ),
        )

    plan = job.payload["plan"]
    video_data = plan["creative_payload"]["object_story_spec"]["video_data"]
    image_hash = video_data["image_hash"]

    assert video_data["video_id"] == "video-123"
    assert image_hash.startswith("dry_run_image_hash_")
    assert plan["creative_asset_id"] == creative.id
    assert plan["facebook_image_hash"] == image_hash

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
            facebook_ads_dry_run=True,
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
                facebook_image_hash="image-hash-123",
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
