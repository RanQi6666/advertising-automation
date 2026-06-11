import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.work_order import WorkOrder
from backend.app.schemas.publishing import AdCreativeDraftRequest
from backend.app.services.publish_service import PublishService


@pytest.mark.asyncio
async def test_ad_creative_draft_maps_work_order_topic_and_copy() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        work_order = WorkOrder(
            id="work-order-1",
            raw_content="投放链接：https://example.com/product",
            parsed_fields={},
            landing_url="https://example.com/product",
        )
        campaign = Campaign(
            id="campaign-1",
            work_order_id=work_order.id,
            name="India TV campaign",
            product_name="India TV",
            metadata_json={},
        )
        topic = ContentTopic(
            id="topic-1",
            campaign_id=campaign.id,
            title="Free IPL Live TV",
            angle="Watch IPL without subscription.",
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

        result = await PublishService().build_ad_creative_draft(
            session,
            AdCreativeDraftRequest(
                campaign_id=campaign.id,
                draft_id=draft.id,
                topic_id=topic.id,
                page_id="page-123",
                facebook_video_id="video-123",
            ),
        )

    assert result["destination_url"] == "https://example.com/product"
    assert result["headline"] == "Free IPL Live TV"
    assert result["primary_text"] == "Watch IPL 2025 live for free."
    assert result["meta_payload"]["body"]["object_story_spec"]["video_data"] == {
        "video_id": "video-123",
        "message": "Watch IPL 2025 live for free.",
        "title": "Free IPL Live TV",
        "call_to_action": {
            "type": "LEARN_MORE",
            "value": {"link": "https://example.com/product"},
        },
    }

    await engine.dispose()
