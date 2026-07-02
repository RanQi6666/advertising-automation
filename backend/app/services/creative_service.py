import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.core.errors import ProviderError
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import CreativeStatus
from backend.app.integrations.image import get_image_provider
from backend.app.integrations.llm import get_llm_provider
from backend.app.schemas.ai import GeneratedImage, ImageBrief
from backend.app.schemas.creative import CreativeGenerateRequest
from backend.app.services.creative_asset_urls import repair_creative_asset_urls
from backend.app.services.generation_task_service import GenerationTaskService
from backend.app.services.image_generation_timing import image_generation_timer
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.model_selection import effective_image_model, settings_for_image_model
from backend.app.services.utils import get_required

STREAM_HEARTBEAT_SECONDS = 5.0


class CreativeService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.llm = get_llm_provider(self.settings)
        self.image_provider = get_image_provider(self.settings)
        self.image_storage = ImageStorageService(self.settings)
        self.text_tasks = GenerationTaskService()

    async def generate_creatives(
        self,
        session: AsyncSession,
        payload: CreativeGenerateRequest,
    ) -> list[CreativeAsset]:
        assets = await self.build_creative_assets_without_commit(
            session=session,
            draft_id=payload.draft_id,
            count=payload.count,
            size=payload.size,
            target_index=payload.target_index,
            target_indices=payload.target_indices,
            extra_metadata={"streamed": False},
            image_model_id=payload.model_id,
            storyboard=payload.storyboard,
            storyboard_text=payload.storyboard_text,
            keyframe_plan=_keyframe_plan(payload),
        )
        for asset in assets:
            session.add(asset)

        await session.commit()
        for asset in assets:
            await session.refresh(asset)
        return assets

    async def build_creative_assets_without_commit(
        self,
        session: AsyncSession,
        draft_id: str,
        count: int,
        size: str,
        extra_metadata: dict,
        target_index: int | None = None,
        target_indices: list[int] | None = None,
        image_model_id: str | None = None,
        storyboard: list[dict] | None = None,
        storyboard_text: str | None = None,
        keyframe_plan: dict | None = None,
        task_id: str | None = None,
    ) -> list[CreativeAsset]:
        draft = await get_required(session, CopyDraft, draft_id)
        creative_strategy = _creative_strategy_from_draft(draft)
        storyboard_context = _storyboard_context(
            storyboard or [],
            storyboard_text,
            keyframe_plan,
            creative_strategy,
        )
        slot_indices = _target_slot_indices(
            count=count,
            target_index=target_index,
            target_indices=target_indices or [],
        )
        briefs = await self._generate_image_briefs_via_text_queue(
            draft=draft,  # type: ignore[arg-type]
            count=len(slot_indices),
            size=size,
            storyboard_context=storyboard_context,
            streamed=False,
            task_id=task_id,
        )
        briefs = _briefs_for_slots(briefs, slot_indices)
        return list(
            await asyncio.gather(
                *[
                    self._generate_asset_from_brief(
                        draft=draft,  # type: ignore[arg-type]
                        brief=brief,
                        version=1,
                        extra_metadata={
                            **extra_metadata,
                            **(
                                {"storyboard_context": storyboard_context}
                                if storyboard_context
                                else {}
                            ),
                            **(
                                {"creative_strategy": creative_strategy}
                                if creative_strategy
                                else {}
                            ),
                            **_keyframe_metadata(brief.image_index, keyframe_plan),
                        },
                        image_model_id=image_model_id,
                        task_id=task_id,
                    )
                    for brief in briefs
                ]
            )
        )

    async def stream_creatives(
        self,
        session: AsyncSession,
        payload: CreativeGenerateRequest,
        task_id: str | None = None,
    ) -> AsyncIterator[dict]:
        draft = await get_required(session, CopyDraft, payload.draft_id)
        slot_indices = _slot_indices(payload)
        keyframe_plan = _keyframe_plan(payload)
        creative_strategy = _creative_strategy_from_draft(draft)
        storyboard_context = _storyboard_context(
            payload.storyboard,
            payload.storyboard_text,
            keyframe_plan,
            creative_strategy,
        )
        yield {"type": "start", "limit": len(slot_indices), "indices": slot_indices}
        for index in slot_indices:
            yield {"type": "slot", "index": index}

        try:
            brief_task = asyncio.create_task(
                self._generate_image_briefs_via_text_queue(
                    draft=draft,  # type: ignore[arg-type]
                    count=len(slot_indices),
                    size=payload.size,
                    storyboard_context=storyboard_context,
                    streamed=True,
                    task_id=task_id,
                )
            )
            while True:
                done, _ = await asyncio.wait(
                    {brief_task},
                    timeout=STREAM_HEARTBEAT_SECONDS,
                )
                if done:
                    break
                yield _heartbeat_event(
                    stage="image_brief_generation",
                    pending_indices=slot_indices,
                )
            briefs = await brief_task
        except Exception as exc:
            for index in slot_indices:
                yield {"type": "error", "index": index, "message": f"图片 brief 生成失败：{exc}"}
            yield {"type": "done", "generated": 0}
            return

        briefs = _briefs_for_slots(briefs, slot_indices)
        missing_indices = slot_indices[len(briefs) :]
        tasks = [
            asyncio.create_task(
                self._generate_asset_result(
                    draft=draft,  # type: ignore[arg-type]
                    brief=brief,
                    version=1,
                    extra_metadata=_creative_metadata(
                        streamed=True,
                        storyboard_context=storyboard_context,
                        keyframe_plan=keyframe_plan,
                        image_index=brief.image_index,
                        creative_strategy=creative_strategy,
                    ),
                    image_model_id=payload.model_id,
                    task_id=task_id,
                )
            )
            for brief in briefs
        ]
        task_indices = {task: brief.image_index for task, brief in zip(tasks, briefs, strict=False)}

        generated_count = 0
        pending_tasks = set(tasks)
        while pending_tasks:
            done_tasks, pending_tasks = await asyncio.wait(
                pending_tasks,
                timeout=STREAM_HEARTBEAT_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done_tasks:
                yield _heartbeat_event(
                    stage="image_generation",
                    pending_indices=_pending_task_indices(pending_tasks, task_indices),
                )
                continue

            for task in sorted(done_tasks, key=lambda item: task_indices.get(item, 0)):
                index, asset, message = await task
                if asset is None:
                    yield {"type": "error", "index": index, "message": message or "图片生成失败。"}
                    continue
                with image_generation_timer(
                    task_id=task_id,
                    draft_id=draft.id,
                    campaign_id=draft.campaign_id,
                    image_index=index,
                    stage="db_commit",
                ):
                    session.add(asset)
                    await session.commit()
                    await session.refresh(asset)
                generated_count += 1
                yield {
                    "type": "asset",
                    "index": index,
                    "asset": _creative_asset_payload(asset),
                }

        for index in missing_indices:
            yield {"type": "error", "index": index, "message": "模型未返回此图片 brief，请重试。"}
        yield {"type": "done", "generated": generated_count}

    async def _generate_image_briefs_via_text_queue(
        self,
        *,
        draft: CopyDraft,
        count: int,
        size: str,
        streamed: bool,
        feedback: str | None = None,
        source_asset: CreativeAsset | None = None,
        storyboard_context: dict | None = None,
        task_id: str | None = None,
    ) -> list[ImageBrief]:
        with image_generation_timer(
            task_id=task_id,
            draft_id=draft.id,
            campaign_id=draft.campaign_id,
            stage="image_brief",
            streamed=streamed,
        ) as finish:
            briefs = await self.text_tasks.run_in_text_queue(
                operation=lambda: self.llm.generate_image_briefs(
                    draft=draft,
                    count=count,
                    size=size,
                    feedback=feedback,
                    source_asset=source_asset,
                    storyboard_context=storyboard_context,
                ),
            )
            finish(status="succeeded", count=len(briefs))
            return briefs

    async def regenerate_creative(
        self,
        session: AsyncSession,
        creative_id: str,
        feedback: str,
        size: str | None = None,
        image_model_id: str | None = None,
    ) -> CreativeAsset:
        source_asset = await get_required(session, CreativeAsset, creative_id)
        draft = await get_required(session, CopyDraft, source_asset.draft_id)
        slot_index = _asset_image_index(source_asset, default=1)
        target_size = size or source_asset.size
        briefs = await self._generate_image_briefs_via_text_queue(
            draft=draft,  # type: ignore[arg-type]
            count=1,
            size=target_size,
            feedback=feedback,
            source_asset=source_asset,  # type: ignore[arg-type]
            streamed=False,
        )
        briefs = _briefs_for_slots(briefs, [slot_index])
        if not briefs:
            raise ProviderError("Image brief revision returned no candidate.")

        asset = await self._generate_asset_from_brief(
            draft=draft,  # type: ignore[arg-type]
            brief=briefs[0],
            version=source_asset.version + 1,
            extra_metadata={
                "streamed": False,
                **(
                    {"creative_strategy": _creative_strategy_from_draft(draft)}
                    if _creative_strategy_from_draft(draft)
                    else {}
                ),
                **_source_keyframe_metadata(source_asset),
                "revision_feedback": feedback,
                "source_creative_asset_id": source_asset.id,
                "source_creative_version": source_asset.version,
            },
            image_model_id=image_model_id,
        )
        if source_asset.status == CreativeStatus.GENERATED.value:
            source_asset.status = CreativeStatus.NEEDS_REVISION.value
        session.add(asset)
        await session.commit()
        await session.refresh(asset)
        return asset

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

    async def _generate_asset_result(
        self,
        draft: CopyDraft,
        brief: ImageBrief,
        version: int,
        extra_metadata: dict,
        image_model_id: str | None = None,
        task_id: str | None = None,
    ) -> tuple[int, CreativeAsset | None, str | None]:
        try:
            asset = await self._generate_asset_from_brief(
                draft=draft,
                brief=brief,
                version=version,
                extra_metadata=extra_metadata,
                image_model_id=image_model_id,
                task_id=task_id,
            )
            return brief.image_index, asset, None
        except Exception as exc:
            return brief.image_index, None, str(exc)

    async def _generate_asset_from_brief(
        self,
        draft: CopyDraft,
        brief: ImageBrief,
        version: int,
        extra_metadata: dict,
        image_model_id: str | None = None,
        task_id: str | None = None,
    ) -> CreativeAsset:
        image_settings = settings_for_image_model(self.settings, image_model_id)
        image_provider = get_image_provider(image_settings)
        image_model = effective_image_model(image_settings)
        with image_generation_timer(
            task_id=task_id,
            draft_id=draft.id,
            campaign_id=draft.campaign_id,
            image_index=brief.image_index,
            keyframe_group=extra_metadata.get("keyframe_group"),
            keyframe_role=extra_metadata.get("keyframe_role"),
            stage="provider_request",
            provider=image_settings.image_provider,
            model=image_model,
        ):
            generated_images = await self.text_tasks.run_in_image_queue(
                lambda: image_provider.generate_images([brief])
            )
        if not generated_images:
            raise ProviderError("Image provider returned no generated image.")
        return await self._asset_from_generated_image(
            draft=draft,
            brief=brief,
            image=generated_images[0],
            version=version,
            extra_metadata={
                **extra_metadata,
                "image_model": image_model,
                "image_provider": image_settings.image_provider,
            },
            task_id=task_id,
        )

    async def _asset_from_generated_image(
        self,
        draft: CopyDraft,
        brief: ImageBrief,
        image: GeneratedImage,
        version: int,
        extra_metadata: dict,
        task_id: str | None = None,
    ) -> CreativeAsset:
        image_url = image.url
        storage_key = image.storage_key
        metadata = {
            **dict(image.metadata),
            **extra_metadata,
            "image_index": brief.image_index,
            "brief": brief.model_dump(mode="json"),
        }
        if image.url:
            metadata["provider_image_url"] = image.url
            if image.storage_key:
                metadata["provider_storage_key"] = image.storage_key
            image_id = str(uuid4())
            with image_generation_timer(
                task_id=task_id,
                draft_id=draft.id,
                campaign_id=draft.campaign_id,
                image_index=brief.image_index,
                keyframe_group=metadata.get("keyframe_group"),
                keyframe_role=metadata.get("keyframe_role"),
                stage="download_storage",
                provider=metadata.get("provider") or metadata.get("image_provider"),
                model=metadata.get("model") or metadata.get("image_model"),
            ):
                image_url, storage_key = await self.image_storage.transfer_provider_image(
                    source_url=image.url,
                    campaign_id=draft.campaign_id,
                    image_id=image_id,
                )
        if not image_url:
            image_url = self.image_storage.public_url_for_storage_key(storage_key)
        return CreativeAsset(
            campaign_id=draft.campaign_id,
            draft_id=draft.id,
            url=image_url,
            storage_key=storage_key,
            prompt=image.prompt,
            alt_text=image.alt_text,
            size=image.size,
            version=version,
            metadata_json=metadata,
        )


def _slot_indices(payload: CreativeGenerateRequest) -> list[int]:
    return _target_slot_indices(
        count=payload.count,
        target_index=payload.target_index,
        target_indices=payload.target_indices,
    )


def _target_slot_indices(
    *,
    count: int,
    target_index: int | None,
    target_indices: list[int],
) -> list[int]:
    if target_indices:
        return list(target_indices)
    if target_index is not None:
        return [target_index]
    return list(range(1, count + 1))


def _briefs_for_slots(briefs: list[ImageBrief], slot_indices: list[int]) -> list[ImageBrief]:
    normalized: list[ImageBrief] = []
    for slot_index, brief in zip(slot_indices, briefs, strict=False):
        normalized.append(
            ImageBrief(
                image_index=slot_index,
                title=brief.title,
                short_text=brief.short_text,
                visual_direction=brief.visual_direction,
                size=brief.size,
            )
        )
    return normalized


def _storyboard_context(
    storyboard: list[dict],
    storyboard_text: str | None,
    keyframe_plan: dict | None = None,
    creative_strategy: dict | None = None,
) -> dict | None:
    clean_scenes = [scene for scene in storyboard if isinstance(scene, dict)]
    clean_text = (storyboard_text or "").strip()
    if not clean_scenes and not clean_text and not keyframe_plan and not creative_strategy:
        return None
    context = {
        "storyboard": clean_scenes[:10],
        "storyboard_text": clean_text[:6000],
    }
    if keyframe_plan:
        context["keyframe_plan"] = keyframe_plan
    if creative_strategy:
        context["creative_strategy"] = creative_strategy
    return context


def _creative_metadata(
    streamed: bool,
    storyboard_context: dict | None = None,
    keyframe_plan: dict | None = None,
    image_index: int | None = None,
    creative_strategy: dict | None = None,
) -> dict:
    metadata = {"streamed": streamed}
    if storyboard_context:
        metadata["storyboard_context"] = storyboard_context
    if creative_strategy:
        metadata["creative_strategy"] = creative_strategy
    if image_index is not None:
        metadata.update(_keyframe_metadata(image_index, keyframe_plan))
    return metadata


def _creative_strategy_from_draft(draft: CopyDraft) -> dict | None:
    metadata = draft.metadata_json if isinstance(draft.metadata_json, dict) else {}
    strategy = metadata.get("creative_strategy")
    return strategy if isinstance(strategy, dict) else None


def _keyframe_plan(payload: CreativeGenerateRequest) -> dict | None:
    if payload.generation_mode != "video_keyframe_variants":
        return None
    return {
        "mode": payload.generation_mode,
        "variant_count": payload.variant_count,
        "frames_per_variant": payload.frames_per_variant,
        "video_duration_seconds": payload.video_duration_seconds,
        "total_images": payload.variant_count * payload.frames_per_variant,
    }


def _keyframe_metadata(image_index: int, keyframe_plan: dict | None) -> dict:
    if not keyframe_plan:
        return {}
    frames_per_variant = int(keyframe_plan.get("frames_per_variant") or 2)
    variant_count = int(keyframe_plan.get("variant_count") or 3)
    group = ((image_index - 1) // frames_per_variant) + 1
    position = ((image_index - 1) % frames_per_variant) + 1
    role = "first_frame" if position == 1 else "last_frame"
    return {
        "generation_mode": keyframe_plan.get("mode"),
        "keyframe_group": group,
        "keyframe_role": role,
        "keyframe_position": position,
        "keyframe_group_size": frames_per_variant,
        "keyframe_variant_count": variant_count,
        "video_duration_seconds": keyframe_plan.get("video_duration_seconds"),
    }


def _source_keyframe_metadata(asset: CreativeAsset) -> dict:
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    keys = (
        "generation_mode",
        "keyframe_group",
        "keyframe_role",
        "keyframe_position",
        "keyframe_group_size",
        "keyframe_variant_count",
        "video_duration_seconds",
    )
    return {key: metadata[key] for key in keys if key in metadata}


def _asset_image_index(asset: CreativeAsset, default: int) -> int:
    metadata = asset.metadata_json if isinstance(asset.metadata_json, dict) else {}
    raw_index = metadata.get("image_index")
    try:
        index = int(raw_index)
    except (TypeError, ValueError):
        return default
    return index if index > 0 else default


def _heartbeat_event(stage: str, pending_indices: list[int]) -> dict:
    return {
        "type": "heartbeat",
        "stage": stage,
        "pending_indices": pending_indices,
        "interval_seconds": STREAM_HEARTBEAT_SECONDS,
    }


def _pending_task_indices(
    tasks: set[asyncio.Task],
    task_indices: dict[asyncio.Task, int],
) -> list[int]:
    return sorted(task_indices.get(task, 0) for task in tasks)


def _creative_asset_payload(asset: CreativeAsset) -> dict:
    from backend.app.schemas.creative import CreativeAssetRead

    return CreativeAssetRead.model_validate(asset).model_dump(mode="json")
