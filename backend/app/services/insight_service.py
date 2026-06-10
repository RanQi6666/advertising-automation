from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.campaign import Campaign
from backend.app.db.models.insight import InsightDaily
from backend.app.schemas.insight import InsightDailyCreate
from backend.app.services.utils import get_required


class InsightService:
    async def create_daily(
        self, session: AsyncSession, payload: InsightDailyCreate
    ) -> InsightDaily:
        await get_required(session, Campaign, payload.campaign_id)
        ctr = Decimal("0")
        cpc: Decimal | None = None
        if payload.impressions:
            ctr = (Decimal(payload.clicks) / Decimal(payload.impressions)) * Decimal("100")
        if payload.clicks:
            cpc = payload.spend / Decimal(payload.clicks)

        insight = InsightDaily(
            **payload.model_dump(),
            ctr=ctr,
            cpc=cpc,
        )
        session.add(insight)
        await session.commit()
        await session.refresh(insight)
        return insight

    async def list_daily(
        self,
        session: AsyncSession,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> list[InsightDaily]:
        result = await session.execute(
            select(InsightDaily)
            .where(InsightDaily.campaign_id == campaign_id)
            .order_by(InsightDaily.metric_date.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())
