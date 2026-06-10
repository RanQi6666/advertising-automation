from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.work_order import WorkOrder
from backend.app.schemas.work_order import WorkOrderCreate
from backend.app.services.work_order_parser import parse_work_order_text


class WorkOrderService:
    async def create_work_order(
        self,
        session: AsyncSession,
        payload: WorkOrderCreate,
    ) -> WorkOrder:
        parsed_fields = parse_work_order_text(payload.raw_content)
        work_order = WorkOrder(
            raw_content=payload.raw_content,
            parsed_fields=parsed_fields,
            project_name=parsed_fields.get("project_name"),
            country=parsed_fields.get("country"),
            media=parsed_fields.get("media"),
            event_name=parsed_fields.get("event_name"),
            product_name=parsed_fields.get("product_name"),
            audience_description=parsed_fields.get("audience_description"),
            landing_url=parsed_fields.get("landing_url"),
            report_timezone=parsed_fields.get("report_timezone"),
            metadata_json=payload.metadata_json,
        )
        session.add(work_order)
        await session.commit()
        await session.refresh(work_order)
        return work_order

    async def list_work_orders(
        self,
        session: AsyncSession,
        limit: int,
        offset: int,
    ) -> list[WorkOrder]:
        result = await session.execute(
            select(WorkOrder).order_by(WorkOrder.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())
