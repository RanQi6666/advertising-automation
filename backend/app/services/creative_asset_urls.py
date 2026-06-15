from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.errors import ProviderError
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.services.image_storage_service import ImageStorageService


def resolve_creative_image_url(
    asset: CreativeAsset,
    image_storage: ImageStorageService,
) -> str | None:
    public_url = image_storage.public_url_for_storage_key(asset.storage_key)
    if public_url:
        return public_url

    storage_key = image_storage.storage_key_for_public_url(asset.url)
    if storage_key:
        return image_storage.public_url_for_storage_key(storage_key)

    return asset.url


async def repair_creative_asset_urls(
    session: AsyncSession,
    assets: Sequence[CreativeAsset],
    image_storage: ImageStorageService,
) -> bool:
    urls_changed = False
    for asset in assets:
        urls_changed = await repair_creative_asset_url(asset, image_storage) or urls_changed
    if urls_changed:
        await session.commit()
    return urls_changed


async def repair_creative_asset_url(
    asset: CreativeAsset,
    image_storage: ImageStorageService,
) -> bool:
    storage_key = _local_storage_key(asset.storage_key) or image_storage.storage_key_for_public_url(
        asset.url
    )
    if storage_key:
        public_url = image_storage.public_url_for_storage_key(storage_key)
        if public_url and (asset.url != public_url or asset.storage_key != storage_key):
            asset.url = public_url
            asset.storage_key = storage_key
            return True
        return False

    source_url = asset.url
    if not source_url:
        if (
            image_storage.settings.object_storage_provider == "local"
            and asset.storage_key is not None
        ):
            asset.storage_key = None
            return True
        return False

    if image_storage.settings.object_storage_provider != "local":
        return False

    if not source_url.startswith(("http://", "https://")):
        changed = asset.url is not None or asset.storage_key is not None
        asset.url = None
        asset.storage_key = None
        return changed

    try:
        repaired_url, repaired_storage_key = await image_storage.transfer_provider_image(
            source_url=source_url,
            campaign_id=asset.campaign_id,
            image_id=asset.id,
        )
    except ProviderError:
        changed = asset.url is not None or asset.storage_key is not None
        asset.url = None
        asset.storage_key = None
        return changed

    asset.url = repaired_url
    asset.storage_key = repaired_storage_key
    return True


def _local_storage_key(storage_key: str | None) -> str | None:
    if storage_key and storage_key.startswith("local://"):
        return storage_key
    return None
