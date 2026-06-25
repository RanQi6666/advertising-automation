import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.db.base import Base
from backend.app.schemas.work_order import WorkOrderCreate
from backend.app.services import work_order_service
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


@pytest.mark.asyncio
async def test_extract_delivery_fields_uses_local_rules_for_standard_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_provider_requested():
        raise AssertionError("standard work order should not call llm provider")

    monkeypatch.setattr(work_order_service, "get_llm_provider", fail_if_provider_requested)

    extraction = await WorkOrderService().extract_delivery_fields(
        "\n".join(
            [
                "工单",
                "项目名称：印度tv8%",
                "投放国家：印度",
                "投放事件：购物",
                "投放人群：男，年龄25-45",
                "投放链接：https://www.mensparadise.store/TV.html",
            ]
        )
    )

    assert extraction.review["source"] == "local_rules"
    assert extraction.review["llm_skipped"] is True
    assert extraction.fields.landing_url.value == "https://www.mensparadise.store/TV.html"
    assert extraction.fields.country.normalized_value == "IN"
    assert extraction.fields.event_name.normalized_value == "PURCHASE"
    assert extraction.fields.gender.normalized_value == "male"
    assert extraction.fields.age_min.value == 25
    assert extraction.fields.age_max.value == 45


@pytest.mark.asyncio
async def test_extract_delivery_fields_maps_registration_variants_to_complete_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_provider_requested():
        raise AssertionError("registration event should be recognized by local rules")

    monkeypatch.setattr(work_order_service, "get_llm_provider", fail_if_provider_requested)

    extraction = await WorkOrderService().extract_delivery_fields(
        "\n".join(
            [
                "Project: registration flow",
                "Country: india",
                "\u4f18\u5316\u4e8b\u4ef6\uff1a\u5feb\u901f\u6ce8\u518c",
                "Audience: male age 25-45",
                "Landing: https://example.com/register",
            ]
        )
    )

    assert extraction.review["source"] == "local_rules"
    assert extraction.fields.event_name.status == "extracted"
    assert extraction.fields.event_name.normalized_value == "COMPLETE_REGISTRATION"


@pytest.mark.asyncio
async def test_extract_delivery_fields_reuses_cached_raw_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_order_service._DELIVERY_EXTRACTION_CACHE.clear()
    calls = 0

    class Provider:
        async def extract_delivery_fields(self, raw_content: str) -> dict:
            nonlocal calls
            calls += 1
            return {
                "schema_version": "ad_delivery_extract_v1",
                "fields": {
                    "landing_url": {
                        "value": "https://example.com",
                        "status": "extracted",
                    },
                    "event_name": {
                        "value": "购物",
                        "normalized_value": "purchase",
                        "status": "extracted",
                    },
                    "country": {
                        "value": "印度",
                        "normalized_value": "IN",
                        "status": "extracted",
                    },
                },
                "review": {},
            }

    monkeypatch.setattr(work_order_service, "get_llm_provider", lambda: Provider())

    service = WorkOrderService()
    first = await service.extract_delivery_fields(
        " 项目名称：缓存测试\r\n投放链接：https://example.com "
    )
    second = await service.extract_delivery_fields("项目名称：缓存测试\n投放链接：https://example.com")

    assert calls == 1
    assert first.fields.landing_url.value == "https://example.com"
    assert second.fields.landing_url.value == "https://example.com"
