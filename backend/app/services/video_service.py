from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import AppError
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.video_asset import VideoAsset
from backend.app.schemas.video import VideoGenerateRequest
from backend.app.services.landing_page_service import LandingPageService, snapshot_to_context
from backend.app.services.utils import get_required


class VideoService:
    def __init__(self) -> None:
        self.landing_pages = LandingPageService()

    async def create_video_job(
        self,
        session: AsyncSession,
        payload: VideoGenerateRequest,
    ) -> VideoAsset:
        await get_required(session, Campaign, payload.campaign_id)
        if payload.draft_id:
            await get_required(session, CopyDraft, payload.draft_id)

        assets = await self._load_source_assets(
            session=session,
            campaign_id=payload.campaign_id,
            asset_ids=payload.creative_asset_ids,
        )
        inferred_draft_id = payload.draft_id or assets[0].draft_id
        landing_page_context = await self._landing_page_context(
            session,
            campaign_id=payload.campaign_id,
        )

        video = VideoAsset(
            campaign_id=payload.campaign_id,
            draft_id=inferred_draft_id,
            source_asset_ids=payload.creative_asset_ids,
            prompt=payload.prompt,
            duration_seconds=payload.duration_seconds,
            aspect_ratio=payload.aspect_ratio,
            metadata_json={
                **payload.metadata_json,
                "implementation_status": "reserved",
                "note": "Video generation provider is not implemented yet.",
                "landing_page": landing_page_context,
            },
        )
        session.add(video)
        await session.commit()
        await session.refresh(video)
        return video

    async def list_videos(
        self,
        session: AsyncSession,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> list[VideoAsset]:
        result = await session.execute(
            select(VideoAsset)
            .where(VideoAsset.campaign_id == campaign_id)
            .order_by(VideoAsset.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def _load_source_assets(
        self,
        session: AsyncSession,
        campaign_id: str,
        asset_ids: list[str],
    ) -> list[CreativeAsset]:
        result = await session.execute(select(CreativeAsset).where(CreativeAsset.id.in_(asset_ids)))
        assets = list(result.scalars().all())
        found_ids = {asset.id for asset in assets}
        missing_ids = [asset_id for asset_id in asset_ids if asset_id not in found_ids]
        if missing_ids:
            raise AppError(f"Creative assets not found: {', '.join(missing_ids)}")

        wrong_campaign_ids = [asset.id for asset in assets if asset.campaign_id != campaign_id]
        if wrong_campaign_ids:
            raise AppError(
                "Creative assets do not belong to the requested campaign: "
                f"{', '.join(wrong_campaign_ids)}"
            )
        return assets

    async def _landing_page_context(
        self,
        session: AsyncSession,
        campaign_id: str,
    ) -> dict | None:
        latest_snapshot = await self.landing_pages.get_latest_snapshot(session, campaign_id)
        return snapshot_to_context(latest_snapshot) if latest_snapshot else None
