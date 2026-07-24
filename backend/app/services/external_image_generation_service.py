import asyncio
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import get_settings
from backend.app.core.errors import AppError, NotFoundError, ProviderError
from backend.app.db.models.generation_task import GenerationTask
from backend.app.integrations.image import get_image_provider
from backend.app.schemas.ai import ImageBrief
from backend.app.schemas.external_image_generation import (
    ExternalGeneratedImageRead,
    ExternalImageGenerationCreate,
    ExternalImageGenerationJobRead,
    ExternalImageRevisionCreate,
)
from backend.app.services.external_image_route_service import (
    route_from_metadata,
    select_external_image_route,
    settings_for_external_image_route,
)
from backend.app.services.generation_task_service import (
    IMAGE_QUEUE_NAME,
    GenerationTaskService,
)
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.model_selection import effective_image_model, settings_for_image_model

EXTERNAL_IMAGE_GENERATION_SOURCE = "external_image_generation"
EXTERNAL_IMAGE_EDIT_SOURCE = "external_image_edit"
EXTERNAL_IMAGE_GENERATION_TASK_TYPE = "external_image_generate"
EXTERNAL_IMAGE_BUSINESS_TYPE = "external_image"


class ExternalImageGenerationService:
    def __init__(self) -> None:
        self.task_service = GenerationTaskService()
        self.image_storage = ImageStorageService()

    async def create_job(
        self,
        session: AsyncSession,
        payload: ExternalImageGenerationCreate,
    ) -> GenerationTask:
        if not payload.prompt.strip():
            raise AppError("prompt is required")

        external_request_id = _clean_text(payload.external_request_id)
        existing = await self._find_existing_task(session, external_request_id)
        if existing is not None:
            existing.reused_existing = True
            return existing

        settings = get_settings()
        route_metadata = await _round_robin_route_metadata(settings)
        business_id = external_request_id or str(uuid4())
        task = await self.task_service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type=EXTERNAL_IMAGE_GENERATION_TASK_TYPE,
            business_type=EXTERNAL_IMAGE_BUSINESS_TYPE,
            business_id=business_id,
            campaign_id=None,
            payload={
                "external_request_id": external_request_id,
                "prompt": payload.prompt,
                "count": payload.count,
                "size": payload.size,
                "model_id": payload.model_id,
            },
            max_attempts=settings.external_image_generation_max_attempts,
            metadata={
                "source": EXTERNAL_IMAGE_GENERATION_SOURCE,
                "external_request_id": external_request_id,
                "count": payload.count,
                "size": payload.size,
                "model_id": payload.model_id,
                **route_metadata,
            },
        )
        return task

    async def create_revision_job(
        self,
        session: AsyncSession,
        source_job_id: str,
        payload: ExternalImageRevisionCreate,
    ) -> GenerationTask:
        feedback = _clean_text(payload.feedback)
        if not feedback:
            raise AppError("feedback is required")

        external_request_id = _clean_text(payload.external_request_id)
        existing = await self._find_existing_task(session, external_request_id)
        if existing is not None:
            existing.reused_existing = True
            return existing

        source_task = await session.get(GenerationTask, source_job_id)
        if (
            source_task is None
            or source_task.task_type != EXTERNAL_IMAGE_GENERATION_TASK_TYPE
        ):
            raise NotFoundError("source image generation job not found")
        if source_task.status != "succeeded":
            raise AppError("source image generation job is not succeeded")

        source_payload = source_task.payload_json or {}
        source_result = source_task.result_json or {}
        source_image = _source_image_for_index(
            source_result,
            payload.source_image_index,
        )
        source_storage_key = _clean_text(source_image.get("storage_key"))
        if not source_storage_key:
            raise AppError("source image storage_key is required")
        reference_image_data_url = self.image_storage.data_url_for_storage_key(
            source_storage_key
        )
        if not reference_image_data_url:
            raise AppError("source image is unavailable")

        source_prompt = (
            _clean_text(source_image.get("prompt"))
            or _clean_text(source_result.get("prompt"))
            or _clean_text(source_payload.get("prompt"))
        )
        if not source_prompt:
            raise AppError("source image prompt is required")

        count = _revision_count(payload.count, source_result, source_payload)
        size = (
            _clean_text(payload.size)
            or _clean_text(source_image.get("size"))
            or _clean_text(source_result.get("size"))
            or _clean_text(source_payload.get("size"))
            or "1:1"
        )
        model_id = (
            _clean_text(payload.model_id)
            or _clean_text(source_result.get("model_id"))
            or _clean_text(source_payload.get("model_id"))
            or _clean_text(source_image.get("model"))
        )
        revised_prompt = f"{source_prompt}\n\n修改要求：{feedback}"
        mode = _initial_revision_mode(model_id)
        business_id = external_request_id or str(uuid4())
        task = await self.task_service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type=EXTERNAL_IMAGE_GENERATION_TASK_TYPE,
            business_type=EXTERNAL_IMAGE_BUSINESS_TYPE,
            business_id=business_id,
            campaign_id=None,
            payload={
                "is_revision": True,
                "external_request_id": external_request_id,
                "source_job_id": source_task.id,
                "source_image_index": payload.source_image_index,
                "source_storage_key": source_storage_key,
                "reference_image_data_url": reference_image_data_url,
                "revision_feedback": feedback,
                "prompt": source_prompt,
                "revised_prompt": revised_prompt,
                "count": count,
                "size": size,
                "model_id": model_id,
            },
            max_attempts=get_settings().external_image_generation_max_attempts,
            metadata={
                "source": EXTERNAL_IMAGE_GENERATION_SOURCE,
                "external_request_id": external_request_id,
                "is_revision": True,
                "source_job_id": source_task.id,
                "source_image_index": payload.source_image_index,
                "count": count,
                "size": size,
                "model_id": model_id,
                "mode": mode,
            },
        )
        return task

    async def create_edit_job(
        self,
        session: AsyncSession,
        *,
        source_storage_key: str,
        prompt: str,
        count: int = 1,
        size: str | None = None,
        model_id: str | None = None,
        external_request_id: str | None = None,
    ) -> GenerationTask:
        cleaned_prompt = _clean_text(prompt)
        if not cleaned_prompt:
            raise AppError("prompt is required")

        cleaned_external_request_id = _clean_text(external_request_id)
        existing = await self._find_existing_task(session, cleaned_external_request_id)
        if existing is not None:
            existing.reused_existing = True
            return existing

        cleaned_source_storage_key = _required_text(
            source_storage_key,
            "source_storage_key",
        )
        image_data_url = self.image_storage.data_url_for_storage_key(
            cleaned_source_storage_key
        )
        if not image_data_url:
            raise AppError("source image is unavailable")

        resolved_count = _revision_count(count, {}, {})
        resolved_size = _clean_text(size) or "1:1"
        cleaned_model_id = _clean_text(model_id)
        business_id = cleaned_external_request_id or str(uuid4())
        task = await self.task_service.create_task(
            session,
            queue_name=IMAGE_QUEUE_NAME,
            task_type=EXTERNAL_IMAGE_GENERATION_TASK_TYPE,
            business_type=EXTERNAL_IMAGE_BUSINESS_TYPE,
            business_id=business_id,
            campaign_id=None,
            payload={
                "mode": "from_image",
                "external_request_id": cleaned_external_request_id,
                "source_storage_key": cleaned_source_storage_key,
                "reference_image_data_url": image_data_url,
                "prompt": cleaned_prompt,
                "count": resolved_count,
                "size": resolved_size,
                "model_id": cleaned_model_id,
            },
            max_attempts=get_settings().external_image_generation_max_attempts,
            metadata={
                "source": EXTERNAL_IMAGE_EDIT_SOURCE,
                "external_request_id": cleaned_external_request_id,
                "mode": "from_image",
                "source_storage_key": cleaned_source_storage_key,
                "count": resolved_count,
                "size": resolved_size,
                "model_id": cleaned_model_id,
            },
        )
        return task

    async def get_job(
        self,
        session: AsyncSession,
        job_id: str,
    ) -> ExternalImageGenerationJobRead:
        task = await session.get(GenerationTask, job_id)
        if task is None or task.task_type != EXTERNAL_IMAGE_GENERATION_TASK_TYPE:
            raise NotFoundError("image generation job not found")
        return self._job_read(task)

    async def execute_task(
        self,
        session: AsyncSession,
        task: GenerationTask,
    ) -> dict:
        payload_json = task.payload_json or {}
        is_revision = bool(payload_json.get("is_revision"))
        payload_mode = _clean_mode(payload_json.get("mode"))
        is_from_image = payload_mode == "from_image"
        if is_revision:
            prompt = _required_text(payload_json.get("revised_prompt"), "prompt")
            count = _revision_count(payload_json.get("count"), {}, {})
            size = _clean_text(payload_json.get("size")) or "1:1"
            model_id = _clean_text(payload_json.get("model_id"))
            external_request_id = _clean_text(payload_json.get("external_request_id"))
            source_job_id = _clean_text(payload_json.get("source_job_id"))
            source_image_index = _positive_int(payload_json.get("source_image_index"), 1)
            source_storage_key = _clean_text(payload_json.get("source_storage_key"))
            reference_image_data_url = _clean_text(
                payload_json.get("reference_image_data_url")
            )
            if not reference_image_data_url:
                reference_image_data_url = self.image_storage.data_url_for_storage_key(
                    source_storage_key
                )
            if not reference_image_data_url:
                raise AppError("source image is unavailable")
            revision_instruction = _required_text(
                payload_json.get("revision_feedback"),
                "feedback",
            )
            task.payload_json = {
                **payload_json,
                "reference_image_data_url": reference_image_data_url,
            }
        elif is_from_image:
            prompt = _required_text(payload_json.get("prompt"), "prompt")
            count = _revision_count(payload_json.get("count"), {}, {})
            size = _clean_text(payload_json.get("size")) or "1:1"
            model_id = _clean_text(payload_json.get("model_id"))
            external_request_id = _clean_text(payload_json.get("external_request_id"))
            source_job_id = None
            source_image_index = None
            source_storage_key = _required_text(
                payload_json.get("source_storage_key"),
                "source_storage_key",
            )
            reference_image_data_url = _clean_text(
                payload_json.get("reference_image_data_url")
            )
            if not reference_image_data_url:
                reference_image_data_url = self.image_storage.data_url_for_storage_key(
                    source_storage_key
                )
            if not reference_image_data_url:
                raise AppError("source image is unavailable")
            revision_instruction = prompt
            task.payload_json = {
                **payload_json,
                "reference_image_data_url": reference_image_data_url,
            }
        else:
            payload = ExternalImageGenerationCreate.model_validate(payload_json)
            if not payload.prompt.strip():
                raise AppError("prompt is required")
            prompt = payload.prompt
            count = payload.count
            size = payload.size
            model_id = payload.model_id
            external_request_id = payload.external_request_id
            source_job_id = None
            source_image_index = None
            reference_image_data_url = None
            revision_instruction = None

        route = route_from_metadata(task.metadata_json)
        if route is None:
            settings = settings_for_image_model(get_settings(), model_id)
        else:
            settings = settings_for_external_image_route(get_settings(), route)
        provider = get_image_provider(settings)
        image_model = effective_image_model(settings)
        briefs = [
            ImageBrief(
                image_index=index,
                title=(
                    "External image revision"
                    if is_revision
                    else (
                        "External uploaded image edit"
                        if is_from_image
                        else "External image generation"
                    )
                ),
                short_text="",
                visual_direction=prompt,
                size=size,
                raw_prompt=prompt,
                reference_image_data_url=reference_image_data_url,
                revision_instruction=revision_instruction,
            )
            for index in range(1, count + 1)
        ]
        generated_lists = await asyncio.gather(
            *(
                self.task_service.run_in_image_provider(
                    lambda brief=brief: provider.generate_images([brief])
                )
                for brief in briefs
            )
        )
        generated_images = [image for images in generated_lists for image in images]
        if len(generated_images) < count:
            raise ProviderError("Image provider returned fewer images than requested.")

        images: list[dict] = []
        result_mode: str | None = None
        for index, image in enumerate(generated_images[:count], start=1):
            public_url, storage_key = await self.image_storage.transfer_external_image(
                source_url=image.url,
                job_id=task.id,
                image_index=index,
                source_storage_key=image.storage_key,
            )
            image_metadata = dict(image.metadata)
            if is_revision:
                mode = _clean_mode(image_metadata.get("mode")) or "generate"
                result_mode = result_mode or mode
                image_metadata.update(
                    {
                        "source_job_id": source_job_id,
                        "source_image_index": source_image_index,
                        "mode": mode,
                    }
                )
            elif is_from_image:
                provider_mode = _clean_mode(image_metadata.get("mode"))
                result_mode = "from_image"
                image_metadata.update(
                    {
                        "mode": "from_image",
                        "provider_mode": provider_mode,
                        "source_storage_key": source_storage_key,
                    }
                )
            images.append(
                {
                    "index": index,
                    "url": public_url,
                    "storage_key": storage_key,
                    "prompt": image.prompt,
                    "size": image.size,
                    "model": image_model,
                    "metadata": {
                        **image_metadata,
                        "source": (
                            EXTERNAL_IMAGE_EDIT_SOURCE
                            if is_from_image
                            else EXTERNAL_IMAGE_GENERATION_SOURCE
                        ),
                        "external_request_id": external_request_id,
                    },
                }
            )

        if is_revision:
            task.metadata_json = {
                **dict(task.metadata_json or {}),
                "source_job_id": source_job_id,
                "source_image_index": source_image_index,
                "mode": result_mode or "generate",
            }
        elif is_from_image:
            task.metadata_json = {
                **dict(task.metadata_json or {}),
                "mode": "from_image",
                "source_storage_key": source_storage_key,
            }

        result = {
            "images": images,
            "generated_count": len(images),
            "count": count,
            "size": size,
            "model_id": image_model if route is not None else model_id,
            "image_model": image_model,
        }
        if is_revision:
            result["source_job_id"] = source_job_id
            result["mode"] = result_mode or "generate"
        elif is_from_image:
            result["mode"] = "from_image"
            result["source_storage_key"] = source_storage_key
        return result

    async def _find_existing_task(
        self,
        session: AsyncSession,
        external_request_id: str | None,
    ) -> GenerationTask | None:
        if not external_request_id:
            return None

        result = await session.execute(
            select(GenerationTask)
            .where(GenerationTask.task_type == EXTERNAL_IMAGE_GENERATION_TASK_TYPE)
            .order_by(GenerationTask.created_at.desc())
        )
        for task in result.scalars().all():
            metadata = task.metadata_json or {}
            if metadata.get("external_request_id") == external_request_id:
                return task
        return None

    def _job_read(self, task: GenerationTask) -> ExternalImageGenerationJobRead:
        payload = task.payload_json or {}
        result = task.result_json or {}
        images = [
            ExternalGeneratedImageRead(
                index=int(image["index"]),
                url=str(image["url"]),
                prompt=str(image.get("prompt") or ""),
            )
            for image in result.get("images", [])
            if isinstance(image, dict) and image.get("url") and image.get("index")
        ]
        return ExternalImageGenerationJobRead(
            job_id=task.id,
            status=_external_status(task),
            images=images if task.status == "succeeded" else [],
            error_message=task.error_message,
            count=int(result.get("count") or payload.get("count") or 1),
            size=str(result.get("size") or payload.get("size") or "1:1"),
            model_id=_clean_text(result.get("model_id") or payload.get("model_id")),
            source_job_id=_clean_text(
                result.get("source_job_id") or payload.get("source_job_id")
            ),
            mode=_clean_mode(
                result.get("mode")
                or payload.get("mode")
                or (task.metadata_json or {}).get("mode")
            ),
        )


async def _round_robin_route_metadata(settings) -> dict[str, dict[str, str | int]]:
    if settings.external_image_route_mode != "round_robin":
        return {}
    route = await select_external_image_route(settings)
    return {"image_route": route.as_metadata()}


def _external_status(task: GenerationTask) -> str:
    if task.status == "succeeded":
        return "succeeded"
    if task.status == "failed":
        return "failed"
    return "processing"


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _source_image_for_index(result: dict[str, Any], source_image_index: int) -> dict[str, Any]:
    images = result.get("images")
    if not isinstance(images, list):
        raise AppError("source image generation job has no images")
    for image in images:
        if not isinstance(image, dict):
            continue
        try:
            image_index = int(image.get("index"))
        except (TypeError, ValueError):
            continue
        if image_index == source_image_index:
            return image
    raise AppError("source image index not found")


def _revision_count(
    explicit_count: object,
    source_result: dict[str, Any],
    source_payload: dict[str, Any],
) -> int:
    raw_count = (
        explicit_count
        if explicit_count is not None
        else source_result.get("count") or source_payload.get("count") or 1
    )
    count = _positive_int(raw_count, 1)
    if count > 5:
        raise AppError("count must be less than or equal to 5")
    return count


def _positive_int(value: object, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _required_text(value: object, field_name: str) -> str:
    text = _clean_text(value)
    if not text:
        raise AppError(f"{field_name} is required")
    return text


def _initial_revision_mode(model_id: str | None) -> str:
    settings = settings_for_image_model(get_settings(), model_id)
    if settings.image_provider == "gateway" and settings.model_gateway_image_edit_enabled:
        return "edit"
    return "generate"


def _clean_mode(value: object) -> str | None:
    text = _clean_text(value)
    return text if text in {"edit", "generate", "from_image"} else None
