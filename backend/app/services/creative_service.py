from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.integrations.image import get_image_provider
from backend.app.integrations.llm import get_llm_provider
from backend.app.schemas.creative import CreativeGenerateRequest
from backend.app.services.creative_asset_urls import repair_creative_asset_urls
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.utils import get_required


class CreativeService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = get_llm_provider(self.settings)
        self.image_provider = get_image_provider(self.settings)
        self.image_storage = ImageStorageService(self.settings)

    async def generate_creatives(
        self,
        session: AsyncSession,
        payload: CreativeGenerateRequest,
    ) -> list[CreativeAsset]:
        draft = await get_required(session, CopyDraft, payload.draft_id)
        briefs = await self.llm.generate_image_briefs(
            draft=draft,  # type: ignore[arg-type]
            count=payload.count,
            size=payload.size,
        )
        generated_images = await self.image_provider.generate_images(briefs)
        assets: list[CreativeAsset] = []
        for image in generated_images:
            image_url = image.url
            storage_key = image.storage_key
            metadata = dict(image.metadata)
            if image.url:
                metadata["provider_image_url"] = image.url
                if image.storage_key:
                    metadata["provider_storage_key"] = image.storage_key
                image_id = str(uuid4())
                image_url, storage_key = await self.image_storage.transfer_provider_image(
                    source_url=image.url,
                    campaign_id=draft.campaign_id,
                    image_id=image_id,
                )
            asset = CreativeAsset(
                campaign_id=draft.campaign_id,
                draft_id=draft.id,
                url=image_url,
                storage_key=storage_key,
                prompt=image.prompt,
                alt_text=image.alt_text,
                size=image.size,
                metadata_json=metadata,
            )
            session.add(asset)
            assets.append(asset)

        await session.commit()
        for asset in assets:
            await session.refresh(asset)
        return assets

    async def list_creatives(
        self,
        session: AsyncSession,
        campaign_id: str,
        limit: int,
        offset: int,
    ) -> list[CreativeAsset]:
        result = await session.execute(
            select(CreativeAsset)
            .where(CreativeAsset.campaign_id == campaign_id)
            .order_by(CreativeAsset.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        assets = list(result.scalars().all())
        await repair_creative_asset_urls(session, assets, self.image_storage)
        return assets
