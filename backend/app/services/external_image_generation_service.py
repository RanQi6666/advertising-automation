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
)
from backend.app.services.generation_task_service import (
    IMAGE_QUEUE_NAME,
    GenerationTaskService,
)
from backend.app.services.image_storage_service import ImageStorageService
from backend.app.services.model_selection import effective_image_model, settings_for_image_model

EXTERNAL_IMAGE_GENERATION_SOURCE = "external_image_generation"
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
            max_attempts=get_settings().external_image_generation_max_attempts,
            metadata={
                "source": EXTERNAL_IMAGE_GENERATION_SOURCE,
                "external_request_id": external_request_id,
                "count": payload.count,
                "size": payload.size,
                "model_id": payload.model_id,
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
        payload = ExternalImageGenerationCreate.model_validate(task.payload_json or {})
        if not payload.prompt.strip():
            raise AppError("prompt is required")

        settings = settings_for_image_model(get_settings(), payload.model_id)
        provider = get_image_provider(settings)
        image_model = effective_image_model(settings)
        briefs = [
            ImageBrief(
                image_index=index,
                title="External image generation",
                short_text="",
                visual_direction=payload.prompt,
                size=payload.size,
                raw_prompt=payload.prompt,
            )
            for index in range(1, payload.count + 1)
        ]
        generated_images = await provider.generate_images(briefs)
        if len(generated_images) < payload.count:
            raise ProviderError("Image provider returned fewer images than requested.")

        images: list[dict] = []
        for index, image in enumerate(generated_images[: payload.count], start=1):
            public_url, storage_key = await self.image_storage.transfer_external_image(
                source_url=image.url,
                job_id=task.id,
                image_index=index,
                source_storage_key=image.storage_key,
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
                        **dict(image.metadata),
                        "source": EXTERNAL_IMAGE_GENERATION_SOURCE,
                        "external_request_id": payload.external_request_id,
                    },
                }
            )

        return {
            "images": images,
            "generated_count": len(images),
            "count": payload.count,
            "size": payload.size,
            "model_id": payload.model_id,
            "image_model": image_model,
        }

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
        )


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
