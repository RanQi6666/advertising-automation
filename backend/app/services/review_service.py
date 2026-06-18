from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import (
    CreativeStatus,
    DraftStatus,
    ReviewDecision,
    ReviewEntityType,
    ReviewStatus,
    TopicStatus,
    VideoStatus,
)
from backend.app.db.models.review import ReviewTask
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.schemas.review import ReviewCreate
from backend.app.services.utils import get_required


class ReviewService:
    async def submit_review(self, session: AsyncSession, payload: ReviewCreate) -> ReviewTask:
        review = ReviewTask(
            campaign_id=payload.campaign_id,
            entity_type=payload.entity_type.value,
            entity_id=payload.entity_id,
            reviewer_id=payload.reviewer_id,
            status=ReviewStatus.COMPLETED.value,
            decision=payload.decision.value,
            feedback=payload.feedback,
            metadata_json=payload.metadata_json,
        )
        session.add(review)
        await self._apply_decision(session, payload)
        await session.commit()
        await session.refresh(review)
        return review

    async def list_pending(
        self,
        session: AsyncSession,
        limit: int,
        offset: int,
        campaign_id: str | None = None,
    ) -> list[ReviewTask]:
        query = select(ReviewTask).where(ReviewTask.status == ReviewStatus.PENDING.value)
        if campaign_id:
            query = query.where(ReviewTask.campaign_id == campaign_id)
        result = await session.execute(
            query.order_by(ReviewTask.created_at.asc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def _apply_decision(self, session: AsyncSession, payload: ReviewCreate) -> None:
        if payload.entity_type == ReviewEntityType.TOPIC:
            topic = await get_required(session, ContentTopic, payload.entity_id)
            if payload.decision == ReviewDecision.APPROVED:
                topic.status = TopicStatus.SELECTED.value
            elif payload.decision == ReviewDecision.REJECTED:
                topic.status = TopicStatus.REJECTED.value
            else:
                topic.status = TopicStatus.PROPOSED.value
            return

        if payload.entity_type == ReviewEntityType.COPY_DRAFT:
            draft = await get_required(session, CopyDraft, payload.entity_id)
            if payload.decision == ReviewDecision.APPROVED:
                draft.status = DraftStatus.APPROVED.value
            elif payload.decision == ReviewDecision.REJECTED:
                draft.status = DraftStatus.REJECTED.value
            else:
                draft.status = DraftStatus.NEEDS_REVISION.value
            return

        if payload.entity_type == ReviewEntityType.CREATIVE_ASSET:
            asset = await get_required(session, CreativeAsset, payload.entity_id)
            if payload.decision == ReviewDecision.APPROVED:
                asset.status = CreativeStatus.APPROVED.value
            elif payload.decision == ReviewDecision.REJECTED:
                asset.status = CreativeStatus.REJECTED.value
            else:
                asset.status = CreativeStatus.NEEDS_REVISION.value
            return

        if payload.entity_type == ReviewEntityType.VIDEO_ASSET:
            video = await get_required(session, VideoAsset, payload.entity_id)
            if payload.decision == ReviewDecision.APPROVED:
                video.status = VideoStatus.APPROVED.value
            elif payload.decision == ReviewDecision.REJECTED:
                video.status = VideoStatus.REJECTED.value
            else:
                video.status = VideoStatus.NEEDS_REVISION.value
            return
