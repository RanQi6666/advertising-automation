from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.brand import Brand
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.client import Client
from backend.app.db.models.work_order import WorkOrder
from backend.app.schemas.campaign import BrandCreate, CampaignCreate, ClientCreate
from backend.app.schemas.work_order import CampaignFromWorkOrderRequest
from backend.app.services.utils import get_required


class CampaignService:
    async def create_client(self, session: AsyncSession, payload: ClientCreate) -> Client:
        client = Client(**payload.model_dump())
        session.add(client)
        await session.commit()
        await session.refresh(client)
        return client

    async def create_brand(self, session: AsyncSession, payload: BrandCreate) -> Brand:
        await get_required(session, Client, payload.client_id)
        brand = Brand(**payload.model_dump())
        session.add(brand)
        await session.commit()
        await session.refresh(brand)
        return brand

    async def create_campaign(self, session: AsyncSession, payload: CampaignCreate) -> Campaign:
        if payload.client_id:
            await get_required(session, Client, payload.client_id)
        if payload.brand_id:
            await get_required(session, Brand, payload.brand_id)
        if payload.work_order_id:
            await get_required(session, WorkOrder, payload.work_order_id)
        campaign = Campaign(**payload.model_dump())
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)
        return campaign

    async def create_campaign_from_work_order(
        self,
        session: AsyncSession,
        work_order_id: str,
        payload: CampaignFromWorkOrderRequest,
    ) -> Campaign:
        work_order = await get_required(session, WorkOrder, work_order_id)
        if payload.client_id:
            await get_required(session, Client, payload.client_id)
        if payload.brand_id:
            await get_required(session, Brand, payload.brand_id)

        parsed_fields = work_order.parsed_fields or {}
        campaign = Campaign(
            client_id=payload.client_id,
            brand_id=payload.brand_id,
            work_order_id=work_order.id,
            name=payload.name or work_order.project_name or "Untitled work order campaign",
            objective=payload.objective or parsed_fields.get("event_name"),
            product_name=payload.product_name or work_order.product_name,
            audience_description=(payload.audience_description or work_order.audience_description),
            budget_notes=_build_budget_notes(parsed_fields),
            metadata_json={
                **payload.metadata_json,
                "work_order": {
                    "raw_content": work_order.raw_content,
                    "parsed_fields": parsed_fields,
                    "country": work_order.country,
                    "media": work_order.media,
                    "landing_url": work_order.landing_url,
                    "report_timezone": work_order.report_timezone,
                },
            },
        )
        session.add(campaign)
        await session.commit()
        await session.refresh(campaign)
        return campaign

    async def list_clients(self, session: AsyncSession, limit: int, offset: int) -> list[Client]:
        result = await session.execute(
            select(Client).order_by(Client.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def list_campaigns(
        self, session: AsyncSession, limit: int, offset: int
    ) -> list[Campaign]:
        result = await session.execute(
            select(Campaign).order_by(Campaign.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def get_campaign(self, session: AsyncSession, campaign_id: str) -> Campaign:
        return await get_required(session, Campaign, campaign_id)  # type: ignore[return-value]


def _build_budget_notes(parsed_fields: dict) -> str | None:
    parts = []
    if parsed_fields.get("payout_amount"):
        parts.append(f"打款金额: {parsed_fields['payout_amount']}")
    if parsed_fields.get("service_fee"):
        parts.append(f"服务费: {parsed_fields['service_fee']}")
    return "\n".join(parts) if parts else None
