import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import Settings
from backend.app.core.errors import ProviderError
from backend.app.db.base import Base
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.services.creative_asset_urls import (
    repair_creative_asset_urls,
    resolve_creative_image_url,
)
from backend.app.services.image_storage_service import ImageStorageService


@pytest.mark.asyncio
async def test_repair_creative_asset_urls_normalizes_and_clears_invalid_links() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(
        object_storage_provider="local",
        public_base_url="http://api.test",
    )
    image_storage = ImageStorageService(settings)

    async def fake_transfer(source_url: str, campaign_id: str, image_id: str) -> tuple[str, str]:
        if source_url == "https://provider.test/repairable.png":
            return (
                "http://api.test/storage/images/campaign-1/repairable.png",
                "local://images/campaign-1/repairable.png",
            )
        raise ProviderError("download failed")

    image_storage.transfer_provider_image = fake_transfer  # type: ignore[method-assign]

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        local_asset = CreativeAsset(
            id="asset-local",
            campaign_id="campaign-1",
            draft_id="draft-1",
            url="http://old-host/storage/images/campaign-1/local.png",
            storage_key="local://images/campaign-1/local.png",
            prompt="Local asset",
            size="1:1",
            metadata_json={},
        )
        repairable_asset = CreativeAsset(
            id="asset-repairable",
            campaign_id="campaign-1",
            draft_id="draft-1",
            url="https://provider.test/repairable.png",
            storage_key=None,
            prompt="Repairable asset",
            size="1:1",
            metadata_json={},
        )
        broken_asset = CreativeAsset(
            id="asset-broken",
            campaign_id="campaign-1",
            draft_id="draft-1",
            url="https://provider.test/broken.png",
            storage_key="volcengine://creative/old",
            prompt="Broken asset",
            size="1:1",
            metadata_json={},
        )
        session.add_all([local_asset, repairable_asset, broken_asset])
        await session.commit()

        changed = await repair_creative_asset_urls(
            session,
            [local_asset, repairable_asset, broken_asset],
            image_storage,
        )

        assert changed is True
        assert local_asset.url == "http://api.test/storage/images/campaign-1/local.png"
        assert local_asset.storage_key == "local://images/campaign-1/local.png"
        assert resolve_creative_image_url(local_asset, image_storage) == local_asset.url

        assert repairable_asset.url == "http://api.test/storage/images/campaign-1/repairable.png"
        assert repairable_asset.storage_key == "local://images/campaign-1/repairable.png"
        assert resolve_creative_image_url(repairable_asset, image_storage) == repairable_asset.url

        assert broken_asset.url is None
        assert broken_asset.storage_key is None
        assert resolve_creative_image_url(broken_asset, image_storage) is None

    await engine.dispose()
