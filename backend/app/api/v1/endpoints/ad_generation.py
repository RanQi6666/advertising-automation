from copy import deepcopy
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status

from backend.app.api.deps import CurrentOperator, DbSession, OptionalOperator
from backend.app.db.models.ad_generation_job import AdGenerationJob
from backend.app.schemas.ad_generation import (
    PublishingAdGenerationJobAccepted,
    PublishingAdGenerationJobCreate,
    PublishingAdGenerationJobRead,
    PublishingAdGenerationReviewConfirm,
    PublishingAdGenerationReviewUpdate,
)
from backend.app.services.ad_generation_service import AdGenerationService
from backend.app.services.collaboration import (
    OperatorContext,
    record_can_edit,
    require_read_access,
)
from backend.app.services.generation_task_service import GenerationTaskService

router = APIRouter()
service = AdGenerationService()
task_service = GenerationTaskService()


@router.post(
    "/integrations/publishing/ad-generation/jobs",
    response_model=PublishingAdGenerationJobAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_publishing_ad_generation_job(
    payload: PublishingAdGenerationJobCreate,
    background_tasks: BackgroundTasks,
    session: DbSession,
    operator: OptionalOperator,
):
    job = await service.create_job(session, payload, operator=operator)
    background_tasks.add_task(service.run_job, job.id)
    return PublishingAdGenerationJobAccepted(
        job_id=job.id,
        status=job.status,
        review_url=service.review_url_for_job(job.id),
    )


@router.get(
    "/integrations/publishing/ad-generation/jobs",
    response_model=list[PublishingAdGenerationJobRead],
)
async def list_publishing_ad_generation_jobs(
    session: DbSession,
    operator: CurrentOperator,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    jobs = await service.list_jobs(
        session,
        status=status_filter,
        limit=limit,
        offset=offset,
        operator=operator,
    )
    return [_job_read(job, operator) for job in jobs]


@router.get(
    "/integrations/publishing/ad-generation/jobs/{job_id}",
    response_model=PublishingAdGenerationJobRead,
)
async def get_publishing_ad_generation_job(
    job_id: str,
    session: DbSession,
    operator: CurrentOperator,
):
    job = await service.get_job(session, job_id)
    require_read_access(job, operator)
    return _job_read(job, operator)


@router.get(
    "/integrations/publishing/ad-generation/jobs/{job_id}/result",
    response_model=dict[str, Any],
)
async def get_publishing_ad_generation_result(job_id: str, session: DbSession):
    job = await service.get_job(session, job_id)
    if job.status != "returned":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Final ad generation result is not ready.",
        )
    return _result_payload(job)


@router.delete(
    "/integrations/publishing/ad-generation/jobs/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_publishing_ad_generation_job(
    job_id: str,
    session: DbSession,
    operator: CurrentOperator,
):
    await service.delete_job(session, job_id, operator=operator)


@router.post(
    "/integrations/publishing/ad-generation/jobs/{job_id}/claim",
    response_model=PublishingAdGenerationJobRead,
)
async def claim_publishing_ad_generation_job(
    job_id: str,
    session: DbSession,
    operator: CurrentOperator,
):
    return _job_read(await service.claim_job(session, job_id, operator), operator)


@router.patch(
    "/integrations/publishing/ad-generation/jobs/{job_id}/review",
    response_model=PublishingAdGenerationJobRead,
)
async def update_publishing_ad_generation_review(
    job_id: str,
    payload: PublishingAdGenerationReviewUpdate,
    session: DbSession,
    operator: CurrentOperator,
):
    return _job_read(
        await service.update_review_payload(session, job_id, payload, operator=operator),
        operator,
    )


@router.post(
    "/integrations/publishing/ad-generation/jobs/{job_id}/confirm",
    response_model=PublishingAdGenerationJobRead,
)
async def confirm_publishing_ad_generation_review(
    job_id: str,
    payload: PublishingAdGenerationReviewConfirm,
    background_tasks: BackgroundTasks,
    session: DbSession,
    operator: CurrentOperator,
):
    job = await service.confirm_review(session, job_id, payload, operator=operator)
    callback_task_id = _queued_callback_task_id(job)
    if callback_task_id:
        background_tasks.add_task(task_service.process_task, callback_task_id)
    return _job_read(job, operator)


def _job_read(
    job: AdGenerationJob,
    operator: OperatorContext | None = None,
) -> PublishingAdGenerationJobRead:
    read = PublishingAdGenerationJobRead.model_validate(job)
    read.review_url = service.review_url_for_job(job.id)
    read.return_url = service.return_url_for_job(job)
    read.can_edit = record_can_edit(job, operator)
    return read


def _queued_callback_task_id(job: AdGenerationJob) -> str | None:
    callback_delivery = (job.metadata_json or {}).get("callback_delivery")
    if not isinstance(callback_delivery, dict):
        return None
    if callback_delivery.get("status") != "queued":
        return None
    task_id = str(callback_delivery.get("task_id") or "")
    return task_id or None


def _result_payload(job: AdGenerationJob) -> dict[str, Any]:
    payload = deepcopy(job.result_payload or {})
    _normalize_material_urls(payload)
    _drop_empty_material_fields(payload)
    payload["job_id"] = job.id
    payload["external_order_id"] = job.external_order_id
    payload["status"] = job.status
    return payload


def _normalize_material_urls(payload: dict[str, Any]) -> None:
    creative_payload = payload.get("creative_payload")
    if isinstance(creative_payload, dict):
        _normalize_creative_material_urls(creative_payload)
        _drop_transient_creative_fields(creative_payload)

    assets = payload.get("assets")
    if isinstance(assets, dict):
        for image in _records(assets.get("images")):
            _normalize_asset_material_urls(image, "image")
            _drop_transient_asset_fields(image)
        for video in _records(assets.get("videos")):
            _normalize_asset_material_urls(video, "video")
            _drop_transient_asset_fields(video)

    _drop_transient_top_level_fields(payload)


def _normalize_creative_material_urls(creative_payload: dict[str, Any]) -> None:
    image_url = _text_or_none(
        creative_payload.get("image_url")
        or creative_payload.get("image_asset_url")
        or creative_payload.get("imageUrl")
        or creative_payload.get("imageAssetUrl")
    )
    video_url = _text_or_none(
        creative_payload.get("video_url")
        or creative_payload.get("video_asset_url")
        or creative_payload.get("videoUrl")
        or creative_payload.get("videoAssetUrl")
    )
    material_url = _text_or_none(
        creative_payload.get("material_url")
        or creative_payload.get("file_url")
        or creative_payload.get("asset_url")
        or creative_payload.get("materialUrl")
        or creative_payload.get("fileUrl")
        or creative_payload.get("assetUrl")
        or creative_payload.get("source_url")
        or creative_payload.get("download_url")
        or video_url
        or image_url
    )

    if material_url:
        creative_payload["asset_url"] = creative_payload.get("asset_url") or material_url
        creative_payload["material_url"] = creative_payload.get("material_url") or material_url
        creative_payload["file_url"] = creative_payload.get("file_url") or material_url
    if image_url:
        creative_payload["image_asset_url"] = creative_payload.get("image_asset_url") or image_url
        creative_payload["image_url"] = creative_payload.get("image_url") or image_url
    if video_url:
        creative_payload["video_asset_url"] = creative_payload.get("video_asset_url") or video_url
        creative_payload["video_url"] = creative_payload.get("video_url") or video_url


def _normalize_asset_material_urls(record: dict[str, Any], asset_type: str) -> None:
    url = _text_or_none(
        record.get("url")
        or record.get("material_url")
        or record.get("file_url")
        or record.get("asset_url")
        or record.get(f"{asset_type}_url")
        or record.get(f"{asset_type}_asset_url")
        or record.get("materialUrl")
        or record.get("fileUrl")
        or record.get("assetUrl")
        or record.get(f"{asset_type}Url")
        or record.get(f"{asset_type}AssetUrl")
    )
    if not url:
        return
    record["url"] = record.get("url") or url
    record["asset_url"] = record.get("asset_url") or url
    record["material_url"] = record.get("material_url") or url
    record["file_url"] = record.get("file_url") or url
    record[f"{asset_type}_url"] = record.get(f"{asset_type}_url") or url
    record[f"{asset_type}_asset_url"] = record.get(f"{asset_type}_asset_url") or url
    record["type"] = record.get("type") or asset_type


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _drop_transient_top_level_fields(payload: dict[str, Any]) -> None:
    _drop_keys(
        payload,
        (
            "asset_url",
            "material_url",
            "file_url",
            "url",
            "src",
            "assetUrl",
            "materialUrl",
            "fileUrl",
            "source_url",
            "download_url",
            "material_address",
            "materialAddress",
            "media_url",
            "mediaUrl",
            "materials",
        ),
    )


def _drop_transient_creative_fields(record: dict[str, Any]) -> None:
    _drop_keys(
        record,
        (
            "url",
            "src",
            "assetUrl",
            "materialUrl",
            "fileUrl",
            "imageUrl",
            "imageAssetUrl",
            "videoUrl",
            "videoAssetUrl",
            "asset_file_url",
            "assetFileUrl",
            "material_file_url",
            "materialFileUrl",
            "source_url",
            "download_url",
            "material_address",
            "materialAddress",
            "media_url",
            "mediaUrl",
            "asset_payload",
            "assetPayload",
            "material_payload",
            "materialPayload",
            "material_info",
            "materialInfo",
            "materials",
            "filename",
            "file_name",
            "fileName",
        ),
    )


def _drop_transient_asset_fields(record: dict[str, Any]) -> None:
    _drop_keys(
        record,
        (
            "src",
            "assetUrl",
            "materialUrl",
            "fileUrl",
            "imageUrl",
            "imageAssetUrl",
            "videoUrl",
            "videoAssetUrl",
            "source_url",
            "download_url",
            "material_address",
            "materialAddress",
            "media_url",
            "mediaUrl",
        ),
    )


def _drop_empty_material_fields(payload: dict[str, Any]) -> None:
    creative_payload = payload.get("creative_payload")
    if isinstance(creative_payload, dict):
        _drop_empty_keys(
            creative_payload,
            (
                "asset_id",
                "assetId",
                "video_url",
                "videoUrl",
                "video_asset_url",
                "videoAssetUrl",
            ),
        )

    for key in ("materials",):
        for record in _records(payload.get(key)):
            _drop_empty_keys(record, ("asset_id", "assetId"))

    assets = payload.get("assets")
    if isinstance(assets, dict):
        for record in [*_records(assets.get("images")), *_records(assets.get("videos"))]:
            _drop_empty_keys(record, ("asset_id", "assetId"))


def _drop_empty_keys(record: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        if key in record and _text_or_none(record.get(key)) is None:
            record.pop(key, None)


def _drop_keys(record: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        record.pop(key, None)


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
