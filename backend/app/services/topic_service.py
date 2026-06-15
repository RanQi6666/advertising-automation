from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.enums import TopicStatus
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.work_order import WorkOrder
from backend.app.integrations.llm import get_llm_provider
from backend.app.schemas.topic import TopicGenerateRequest
from backend.app.services.landing_page_service import (
    LandingPageService,
    snapshot_to_context,
)
from backend.app.services.utils import get_required


class TopicService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = get_llm_provider(self.settings)
        self.landing_pages = LandingPageService()

    async def generate_topics(
        self,
        session: AsyncSession,
        payload: TopicGenerateRequest,
    ) -> list[ContentTopic]:
        campaign = await get_required(session, Campaign, payload.campaign_id)
        effective_signals = await self._build_effective_signals(
            session=session,
            campaign=campaign,  # type: ignore[arg-type]
            request_signals=payload.signals,
        )
        candidates = await self.llm.generate_topics(
            campaign=campaign,  # type: ignore[arg-type]
            limit=payload.limit,
            signals=effective_signals,
        )
        topics: list[ContentTopic] = []
        for candidate in candidates:
            topic = ContentTopic(
                campaign_id=payload.campaign_id,
                title=candidate.title,
                angle=candidate.angle,
                audience=candidate.audience,
                selling_points=candidate.selling_points,
                risk_notes=candidate.risk_notes,
                rationale=candidate.rationale,
                score=candidate.score,
                source_data={
                    "signals": effective_signals,
                    "provider": self.settings.llm_provider,
                    "model": self.settings.llm_model,
                },
            )
            session.add(topic)
            topics.append(topic)

        await session.commit()
        for topic in topics:
            await session.refresh(topic)
        return topics

    async def list_topics(
        self,
        session: AsyncSession,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> list[ContentTopic]:
        result = await session.execute(
            select(ContentTopic)
            .where(ContentTopic.campaign_id == campaign_id)
            .order_by(ContentTopic.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def select_topic(self, session: AsyncSession, topic_id: str) -> ContentTopic:
        topic = await get_required(session, ContentTopic, topic_id)
        await session.execute(
            update(ContentTopic)
            .where(
                ContentTopic.campaign_id == topic.campaign_id,
                ContentTopic.id != topic.id,
                ContentTopic.status == TopicStatus.SELECTED.value,
            )
            .values(status=TopicStatus.PROPOSED.value)
        )
        topic.status = TopicStatus.SELECTED.value
        await session.commit()
        await session.refresh(topic)
        return topic  # type: ignore[return-value]

    async def _build_effective_signals(
        self,
        session: AsyncSession,
        campaign: Campaign,
        request_signals: dict,
    ) -> dict:
        signals = dict(request_signals)
        work_order_context = campaign.metadata_json.get("work_order")

        if not work_order_context and campaign.work_order_id:
            work_order = await get_required(session, WorkOrder, campaign.work_order_id)
            work_order_context = {
                "raw_content": work_order.raw_content,
                "parsed_fields": work_order.parsed_fields,
                "country": work_order.country,
                "media": work_order.media,
                "landing_url": work_order.landing_url,
                "report_timezone": work_order.report_timezone,
            }

        if work_order_context:
            signals["work_order"] = work_order_context

        landing_page_context = campaign.metadata_json.get("landing_page")
        if not landing_page_context:
            latest_snapshot = await self.landing_pages.get_latest_snapshot(session, campaign.id)
            if latest_snapshot:
                landing_page_context = snapshot_to_context(latest_snapshot)
            elif _landing_url_from_context(work_order_context):
                snapshot = await self.landing_pages.analyze_campaign_landing_page(
                    session,
                    campaign.id,
                )
                landing_page_context = snapshot_to_context(snapshot)

        if landing_page_context:
            signals["landing_page"] = landing_page_context
        return signals

    async def reject_topic(self, session: AsyncSession, topic_id: str) -> ContentTopic:
        topic = await get_required(session, ContentTopic, topic_id)
        topic.status = TopicStatus.REJECTED.value
        await session.commit()
        await session.refresh(topic)
        return topic  # type: ignore[return-value]


def _landing_url_from_context(work_order_context: dict | None) -> str | None:
    if not work_order_context:
        return None
    parsed_fields = work_order_context.get("parsed_fields") or {}
    return work_order_context.get("landing_url") or parsed_fields.get("landing_url")
