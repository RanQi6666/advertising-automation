import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.services.generation_task_service as task_module
from backend.app.core.config import get_settings
from backend.app.db.base import Base, utcnow
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.db.session import get_session
from backend.app.main import create_app
from backend.app.schemas.external_image_generation import (
    ExternalImageGenerationCreate,
    ExternalImageRevisionCreate,
)
from backend.app.services.external_image_generation_service import (
    ExternalImageGenerationService,
)


@pytest.fixture(autouse=True)
def external_image_generation_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_PROVIDER", "placeholder")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ai.example.test")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "background_tasks")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _session_factory(tmp_path, filename: str = "external-image-generation.db"):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / filename).as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _client_with_db(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    token: str | None = None,
):
    if token:
        monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", token)
    else:
        monkeypatch.delenv("AI_ADS_ACCESS_TOKEN", raising=False)
    get_settings.cache_clear()

    engine, session_factory = await _session_factory(tmp_path)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    return client, engine, app


def _authorized_headers(token: str = "image-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _image_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "external_request_id": "external-image-1",
        "prompt": "A premium mobile game lobby, cinematic lighting, no text",
        "count": 1,
        "size": "1:1",
    }
    payload.update(overrides)
    return payload


async def _count_rows(engine, model) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


@pytest.mark.asyncio
async def test_external_image_generation_creates_async_job_and_polling_returns_storage_urls(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="image-token")
    try:
        create_response = client.post(
            "/api/v1/integrations/image-generation/images",
            headers=_authorized_headers(),
            json=_image_payload(count=2),
        )
        create_body = create_response.json()
        created = create_body["data"]

        poll_response = client.get(
            f"/api/v1/integrations/image-generation/jobs/{created['job_id']}",
            headers=_authorized_headers(),
        )
        poll_body = poll_response.json()
        polled = poll_body["data"]
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert create_response.status_code == 202
    assert create_body["code"] == 1001
    assert create_body["message"] == "processing"
    assert created["status"] == "processing"
    assert created["count"] == 2
    assert created["size"] == "1:1"
    assert poll_response.status_code == 200
    assert poll_body["code"] == 0
    assert poll_body["message"] == "success"
    assert polled["job_id"] == created["job_id"]
    assert polled["status"] == "succeeded"
    assert [image["index"] for image in polled["images"]] == [1, 2]
    for image in polled["images"]:
        assert image["url"].startswith("https://ai.example.test/storage/")
        assert f"/images/external_image_generation/{created['job_id']}/" in image["url"]

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        tasks = (await session.execute(select(GenerationTask))).scalars().all()
    assert len(tasks) == 1
    assert tasks[0].task_type == "external_image_generate"
    assert tasks[0].queue_name == "image_queue"
    assert tasks[0].business_type == "external_image"
    assert tasks[0].campaign_id is None
    assert tasks[0].payload_json["prompt"] == _image_payload()["prompt"]

    assert await _count_rows(engine, Campaign) == 0
    assert await _count_rows(engine, ContentTopic) == 0
    assert await _count_rows(engine, CopyDraft) == 0
    assert await _count_rows(engine, CreativeAsset) == 0
    assert await _count_rows(engine, VideoAsset) == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_image_generation_max_attempts_uses_settings(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXTERNAL_IMAGE_GENERATION_MAX_ATTEMPTS", "2")
    get_settings.cache_clear()
    engine, session_factory = await _session_factory(
        tmp_path,
        filename="external-image-generation-attempts.db",
    )

    async with session_factory() as session:
        task = await ExternalImageGenerationService().create_job(
            session,
            ExternalImageGenerationCreate.model_validate(
                _image_payload(external_request_id="configured-attempts")
            ),
        )

    assert task.max_attempts == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_image_revision_service_creates_job_from_source_image(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_factory = await _session_factory(
        tmp_path,
        filename="external-image-revision-service.db",
    )
    storage_root = tmp_path / "storage"
    source_bytes = b"source image bytes"

    async with session_factory() as session:
        service = ExternalImageGenerationService()
        source_task = await service.create_job(
            session,
            ExternalImageGenerationCreate.model_validate(
                _image_payload(
                    external_request_id="source-image-job",
                    prompt="Original prompt",
                    size="4:5",
                    model_id="source-image-model",
                )
            ),
        )
        source_relative_path = (
            storage_root
            / "images"
            / "external_image_generation"
            / source_task.id
            / "1.png"
        )
        source_relative_path.parent.mkdir(parents=True, exist_ok=True)
        source_relative_path.write_bytes(source_bytes)
        source_storage_key = (
            f"local://images/external_image_generation/{source_task.id}/1.png"
        )
        source_task.status = "succeeded"
        source_task.finished_at = utcnow()
        source_task.result_json = {
            "images": [
                {
                    "index": 1,
                    "url": "https://ai.example.test/storage/source.png",
                    "storage_key": source_storage_key,
                    "prompt": "Original prompt",
                    "size": "4:5",
                    "model": "source-image-model",
                }
            ],
            "count": 1,
            "size": "4:5",
            "model_id": "source-image-model",
        }
        await session.commit()
        await session.refresh(source_task)

        revision_task = await service.create_revision_job(
            session,
            source_task.id,
            ExternalImageRevisionCreate.model_validate(
                {
                    "external_request_id": "revision-image-job",
                    "feedback": "把背景改成红色",
                }
            ),
        )
        result = await service.execute_task(session, revision_task)

    revised_prompt = "Original prompt\n\n修改要求：把背景改成红色"
    expected_data_url = (
        "data:image/png;base64," + base64.b64encode(source_bytes).decode("ascii")
    )
    assert revision_task.queue_name == "image_queue"
    assert revision_task.task_type == "external_image_generate"
    assert revision_task.business_type == "external_image"
    assert revision_task.payload_json["is_revision"] is True
    assert revision_task.payload_json["source_job_id"] == source_task.id
    assert revision_task.payload_json["source_image_index"] == 1
    assert revision_task.payload_json["source_storage_key"] == source_storage_key
    assert revision_task.payload_json["reference_image_data_url"] == expected_data_url
    assert revision_task.payload_json["prompt"] == "Original prompt"
    assert revision_task.payload_json["revised_prompt"] == revised_prompt
    assert revision_task.payload_json["size"] == "4:5"
    assert revision_task.payload_json["model_id"] == "source-image-model"
    assert revision_task.metadata_json["source_job_id"] == source_task.id
    assert revision_task.metadata_json["mode"] == "generate"
    assert result["images"][0]["prompt"] == revised_prompt
    assert result["images"][0]["metadata"]["source_job_id"] == source_task.id
    assert result["images"][0]["metadata"]["mode"] == "generate"
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_image_revision_endpoint_returns_new_job_and_polling_reuses_job_api(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="image-token")
    try:
        source_response = client.post(
            "/api/v1/integrations/image-generation/images",
            headers=_authorized_headers(),
            json=_image_payload(
                external_request_id="source-for-revision",
                prompt="Original prompt",
            ),
        )
        source_job_id = source_response.json()["data"]["job_id"]

        revision_response = client.post(
            f"/api/v1/integrations/image-generation/jobs/{source_job_id}/revisions",
            headers=_authorized_headers(),
            json={
                "external_request_id": "revision-via-api",
                "feedback": "把背景改成红色",
            },
        )
        revision_data = revision_response.json()["data"]

        poll_response = client.get(
            f"/api/v1/integrations/image-generation/jobs/{revision_data['job_id']}",
            headers=_authorized_headers(),
        )
        polled = poll_response.json()["data"]
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert source_response.status_code == 202
    assert revision_response.status_code == 202
    assert revision_data["job_id"] != source_job_id
    assert revision_data["status"] == "processing"
    assert revision_data["source_job_id"] == source_job_id
    assert revision_data["count"] == 1
    assert revision_data["size"] == "1:1"
    assert poll_response.status_code == 200
    assert polled["job_id"] == revision_data["job_id"]
    assert polled["status"] == "succeeded"
    assert polled["source_job_id"] == source_job_id
    assert polled["mode"] == "generate"
    assert polled["images"][0]["prompt"] == "Original prompt\n\n修改要求：把背景改成红色"
    assert await _count_rows(engine, GenerationTask) == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_image_generation_requires_token_and_allows_query_token(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="image-token")
    try:
        rejected = client.post(
            "/api/v1/integrations/image-generation/images",
            json=_image_payload(external_request_id="no-token"),
        )
        accepted = client.post(
            "/api/v1/integrations/image-generation/images?ai_access_token=image-token",
            json=_image_payload(external_request_id="query-token"),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert rejected.status_code == 401
    assert accepted.status_code == 202
    assert accepted.json()["code"] == 1001


@pytest.mark.asyncio
async def test_external_image_generation_requires_prompt(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="image-token")
    try:
        payload = _image_payload()
        payload.pop("prompt")
        response = client.post(
            "/api/v1/integrations/image-generation/images",
            headers=_authorized_headers(),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    assert response.json()["code"] == 4001


@pytest.mark.asyncio
async def test_external_image_generation_reuses_duplicate_external_request_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="image-token")
    payload = _image_payload(external_request_id="external-image-idempotent")
    try:
        first = client.post(
            "/api/v1/integrations/image-generation/images",
            headers=_authorized_headers(),
            json=payload,
        )
        second = client.post(
            "/api/v1/integrations/image-generation/images",
            headers=_authorized_headers(),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["job_id"] == second.json()["data"]["job_id"]
    assert await _count_rows(engine, GenerationTask) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_image_generation_passes_external_prompt_to_provider_unchanged(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    external_prompt = (
        "RAW prompt: keep Facebook text, no template, no safety wrapper, exact punctuation!"
    )
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="image-token")
    try:
        create_response = client.post(
            "/api/v1/integrations/image-generation/images",
            headers=_authorized_headers(),
            json=_image_payload(prompt=external_prompt),
        )
        job_id = create_response.json()["data"]["job_id"]
        poll_response = client.get(
            f"/api/v1/integrations/image-generation/jobs/{job_id}",
            headers=_authorized_headers(),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert create_response.status_code == 202
    assert poll_response.status_code == 200
    image = poll_response.json()["data"]["images"][0]
    assert image["prompt"] == external_prompt
    assert "Creative safety hard rules" not in image["prompt"]
    assert await _count_rows(engine, Campaign) == 0
    assert await _count_rows(engine, ContentTopic) == 0
    assert await _count_rows(engine, CopyDraft) == 0
    assert await _count_rows(engine, CreativeAsset) == 0
    assert await _count_rows(engine, VideoAsset) == 0
    await engine.dispose()
