from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.enums import DraftStatus
from backend.app.db.models.topic import ContentTopic
from backend.app.integrations.llm import get_llm_provider
from backend.app.integrations.llm.language import build_target_language_context
from backend.app.schemas.copywriting import CopyGenerateRequest, CopyReviseRequest
from backend.app.services.landing_page_service import LandingPageService, snapshot_to_context
from backend.app.services.utils import get_required


class CopywritingService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = get_llm_provider(self.settings)
        self.landing_pages = LandingPageService()

    async def generate_copy(
        self,
        session: AsyncSession,
        payload: CopyGenerateRequest,
    ) -> CopyDraft:
        topic = await get_required(session, ContentTopic, payload.topic_id)
        campaign = await get_required(session, Campaign, topic.campaign_id)
        landing_page_context = await self._ensure_landing_page_context(
            session,
            campaign,  # type: ignore[arg-type]
        )
        target_language = build_target_language_context(
            campaign=campaign,
            context={"landing_page": landing_page_context},
        )
        candidate = await self.llm.generate_copy(
            campaign=campaign,  # type: ignore[arg-type]
            topic=topic,  # type: ignore[arg-type]
            constraints=payload.constraints,
        )
        draft = CopyDraft(
            campaign_id=topic.campaign_id,
            topic_id=topic.id,
            body=candidate.body,
            primary_text=candidate.primary_text,
            headline=candidate.headline,
            description=candidate.description,
            cta=candidate.cta,
            model_name=self.settings.llm_model,
            prompt_version="copywriting.v1",
            metadata_json={
                **({"landing_page": landing_page_context} if landing_page_context else {}),
                "target_language": target_language,
            },
        )
        session.add(draft)
        await session.commit()
        await session.refresh(draft)
        return draft

    async def revise_copy(
        self,
        session: AsyncSession,
        draft_id: str,
        payload: CopyReviseRequest,
    ) -> CopyDraft:
        draft = await get_required(session, CopyDraft, draft_id)
        topic = await get_required(session, ContentTopic, draft.topic_id)
        campaign = await get_required(session, Campaign, draft.campaign_id)
        target_language = build_target_language_context(
            campaign=campaign,
            draft_metadata=draft.metadata_json,
        )
        candidate = await self.llm.revise_copy(
            campaign=campaign,  # type: ignore[arg-type]
            topic=topic,  # type: ignore[arg-type]
            draft=draft,  # type: ignore[arg-type]
            feedback=payload.feedback,
            constraints=payload.constraints,
        )
        draft.status = DraftStatus.NEEDS_REVISION.value
        revised = CopyDraft(
            campaign_id=draft.campaign_id,
            topic_id=draft.topic_id,
            body=candidate.body,
            primary_text=candidate.primary_text,
            headline=candidate.headline,
            description=candidate.description,
            cta=candidate.cta,
            version=draft.version + 1,
            model_name=self.settings.llm_model,
            prompt_version="copywriting.v1",
            metadata_json={
                "revision_feedback": payload.feedback,
                "previous_draft_id": draft.id,
                "target_language": target_language,
            },
        )
        session.add(revised)
        await session.commit()
        await session.refresh(revised)
        return revised

    async def list_drafts(
        self,
        session: AsyncSession,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> list[CopyDraft]:
        result = await session.execute(
            select(CopyDraft)
            .where(CopyDraft.campaign_id == campaign_id)
            .order_by(CopyDraft.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def _ensure_landing_page_context(
        self,
        session: AsyncSession,
        campaign: Campaign,
    ) -> dict | None:
        landing_page_context = campaign.metadata_json.get("landing_page")
        if landing_page_context:
            return landing_page_context

        latest_snapshot = await self.landing_pages.get_latest_snapshot(session, campaign.id)
        if latest_snapshot:
            return snapshot_to_context(latest_snapshot)

        work_order_context = campaign.metadata_json.get("work_order") or {}
        parsed_fields = work_order_context.get("parsed_fields") or {}
        if work_order_context.get("landing_url") or parsed_fields.get("landing_url"):
            snapshot = await self.landing_pages.analyze_campaign_landing_page(
                session,
                campaign.id,
            )
            return snapshot_to_context(snapshot)
        return None
