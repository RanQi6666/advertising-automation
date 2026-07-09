from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from backend.app.db.models.enums import VideoStatus  # noqa: E402
from backend.app.db.models.video_asset import VideoAsset  # noqa: E402
from backend.app.db.session import AsyncSessionLocal, engine  # noqa: E402
from backend.app.services.external_sources import EXTERNAL_VIDEO_GENERATION_SOURCE  # noqa: E402
from backend.app.services.video_service import VideoService  # noqa: E402


async def main() -> None:
    args = _parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("--apply and --dry-run cannot be used together.")

    dry_run = not args.apply
    service = VideoService()
    if service.settings.video_provider != "volcengine":
        raise SystemExit(
            "This repair script must run with VIDEO_PROVIDER=volcengine "
            f"(current: {service.settings.video_provider})."
        )

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(_video_query(args))
            videos = [
                video
                for video in result.scalars().all()
                if args.all_sources
                or _metadata(video).get("source") == EXTERNAL_VIDEO_GENERATION_SOURCE
            ]
            scope = (
                "all provider-backed videos"
                if args.all_sources
                else EXTERNAL_VIDEO_GENERATION_SOURCE
            )
            print(
                f"mode={'dry-run' if dry_run else 'apply'} "
                f"scope={scope} "
                f"records={len(videos)}"
            )
            for video in videos:
                await _repair_video(
                    session=session,
                    service=service,
                    video=video,
                    dry_run=dry_run,
                    force=args.force,
                )
    finally:
        await engine.dispose()


def _video_query(args: argparse.Namespace):
    statement = (
        select(VideoAsset)
        .where(VideoAsset.provider_job_id.is_not(None))
        .order_by(VideoAsset.created_at.desc())
    )
    if args.video_id:
        statement = statement.where(VideoAsset.id.in_(args.video_id))
    if args.limit is not None:
        statement = statement.limit(args.limit)
    return statement


async def _repair_video(
    *,
    session: AsyncSession,
    service: VideoService,
    video: VideoAsset,
    dry_run: bool,
    force: bool,
) -> None:
    provider_job_id = str(video.provider_job_id or "").strip()
    if not provider_job_id:
        return

    metadata = _metadata(video)
    old_url = _text_or_none(video.url)
    old_storage_key = _text_or_none(video.storage_key)
    old_provider_url = _text_or_none(metadata.get("provider_video_url"))
    provider_status = await service.video_provider.get_generation_status(provider_job_id)
    new_provider_url = _text_or_none(provider_status.video_url)
    changed = bool(new_provider_url and new_provider_url != (old_provider_url or old_url))
    can_transfer = provider_status.provider_status == "succeeded" and bool(new_provider_url)
    will_update = can_transfer and (force or changed or not old_storage_key)

    print(
        "video_id={video_id} provider_job_id={provider_job_id} "
        "external_request_id={external_request_id} status={status} "
        "old_url={old_url} old_provider_url={old_provider_url} "
        "new_provider_url={new_provider_url} changed={changed} will_update={will_update}".format(
            video_id=video.id,
            provider_job_id=provider_job_id,
            external_request_id=metadata.get("external_request_id"),
            status=provider_status.provider_status,
            old_url=old_url,
            old_provider_url=old_provider_url,
            new_provider_url=new_provider_url,
            changed=changed,
            will_update=will_update,
        )
    )

    if dry_run or not will_update:
        return

    video.url = new_provider_url
    video.storage_key = None
    video.status = VideoStatus.GENERATED.value
    video.error_message = provider_status.error_message
    video.metadata_json = {
        **metadata,
        "implementation_status": "provider_video_url_repaired",
        "provider_status": provider_status.provider_status,
        "provider_status_response": provider_status.raw_response,
        "provider_video_url": new_provider_url,
        "last_frame_url": provider_status.last_frame_url,
        "video_transfer_status": "repair_pending",
        "storage_note": "Provider video URL was refreshed by repair script.",
    }
    await session.commit()
    await session.refresh(video)
    transferred = await service.transfer_completed_video(session, video.id)
    print(
        f"updated video_id={transferred.id} "
        f"stored_url={transferred.url} "
        f"storage_key={transferred.storage_key}"
    )


def _metadata(video: VideoAsset) -> dict[str, Any]:
    return video.metadata_json if isinstance(video.metadata_json, dict) else {}


def _text_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Repair stored external video URLs by re-querying Volcengine with each "
            "video provider_job_id. Default mode is dry-run."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write repaired provider URLs and transfer videos to configured storage.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned changes without writing. This is also the default.",
    )
    parser.add_argument(
        "--all-sources",
        action="store_true",
        help="Include all provider-backed videos instead of only external_video_generation.",
    )
    parser.add_argument(
        "--video-id",
        action="append",
        default=[],
        help="Limit repair to a specific video_assets.id. May be provided multiple times.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Maximum number of provider-backed videos to scan.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-transfer even when the provider URL matches stored metadata.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main())
