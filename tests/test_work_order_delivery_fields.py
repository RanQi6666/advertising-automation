import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.schemas.work_order import WorkOrderCreate
from backend.app.services.work_order_service import WorkOrderService


@pytest.mark.asyncio
async def test_create_work_order_uses_reviewed_delivery_fields() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        payload = WorkOrderCreate(
            raw_content="项目名称：测试项目\n投放地址：https://example.com/raw",
            llm_delivery_fields={
                "schema_version": "ad_delivery_extract_v1",
                "fields": {
                    "landing_url": {
                        "value": "https://example.com/raw",
                        "status": "extracted",
                    }
                },
            },
            reviewed_delivery_fields={
                "landing_url": "https://example.com/confirmed",
                "event_name": "购物",
                "country": "印度",
                "age_min": "25",
                "age_max": "45",
                "gender": "男",
                "audience_description_raw": "",
            },
        )

        work_order = await WorkOrderService().create_work_order(session, payload)

    assert work_order.landing_url == "https://example.com/confirmed"
    assert work_order.event_name == "购物"
    assert work_order.country == "印度"
    assert work_order.audience_description == "男。年龄25-45"
    assert work_order.metadata_json["reviewed_delivery_fields"]["landing_url"] == (
        "https://example.com/confirmed"
    )
    assert work_order.metadata_json["llm_delivery_fields"]["schema_version"] == (
        "ad_delivery_extract_v1"
    )
