from datetime import UTC
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.core.errors import ProviderError
from backend.app.db.base import utcnow
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.enums import PublishChannel, PublishStatus
from backend.app.db.models.publish_job import PublishJob
from backend.app.integrations.facebook.graph_client import FacebookGraphClient
from backend.app.schemas.publishing import PublishJobCreate
from backend.app.services.utils import get_required


class PublishService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.facebook = FacebookGraphClient(self.settings)

    async def create_job(self, session: AsyncSession, payload: PublishJobCreate) -> PublishJob:
        await get_required(session, Campaign, payload.campaign_id)
        if payload.draft_id:
            await get_required(session, CopyDraft, payload.draft_id)
        job = PublishJob(**payload.model_dump())
        session.add(job)
        await session.commit()
        await session.refresh(job)
        return job

    async def list_jobs(
        self,
        session: AsyncSession,
        limit: int,
        offset: int,
        campaign_id: str | None = None,
    ) -> list[PublishJob]:
        query = select(PublishJob)
        if campaign_id:
            query = query.where(PublishJob.campaign_id == campaign_id)
        result = await session.execute(
            query.order_by(PublishJob.created_at.desc()).limit(limit).offset(offset)
        )
        return list(result.scalars().all())

    async def publish_job(self, session: AsyncSession, job_id: str) -> PublishJob:
        job = await get_required(session, PublishJob, job_id)
        job.status = PublishStatus.PUBLISHING.value
        await session.flush()

        try:
            response = await self._publish(session, job)  # type: ignore[arg-type]
            job.external_id = response.get("id", str(uuid4()))
            job.metadata_json = {**job.metadata_json, "provider_response": response}
            job.status = PublishStatus.PUBLISHED.value
            job.published_at = utcnow().astimezone(UTC)
        except ProviderError as exc:
            job.status = PublishStatus.FAILED.value
            job.error_message = str(exc)

        await session.commit()
        await session.refresh(job)
        return job  # type: ignore[return-value]

    async def _publish(self, session: AsyncSession, job: PublishJob) -> dict:
        if job.channel == PublishChannel.FACEBOOK_PAGE.value:
            message = job.payload.get("message")
            if not message and job.draft_id:
                draft = await get_required(session, CopyDraft, job.draft_id)
                message = draft.primary_text or draft.body

            page_id = job.payload.get("page_id") or "dry-run-page"
            access_token_ref = job.payload.get("access_token_ref")
            image_url = job.payload.get("image_url")
            if image_url:
                return await self.facebook.publish_photo_post(
                    page_id=page_id,
                    image_url=image_url,
                    caption=message or "",
                    access_token_ref=access_token_ref,
                )
            return await self.facebook.publish_page_post(
                page_id=page_id,
                message=message or "",
                access_token_ref=access_token_ref,
            )

        if job.channel == PublishChannel.FACEBOOK_AD.value and self.settings.facebook_dry_run:
            return {"id": f"dry_run_ad_{uuid4()}", "dry_run": True, "payload": job.payload}

        raise ProviderError(f"Publishing channel is not implemented: {job.channel}")
