import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.core.errors import AppError, ProviderError
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import VideoStatus
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.models.video_asset import VideoAsset
from backend.app.db.models.work_order import WorkOrder
from backend.app.db.session import get_session
from backend.app.integrations.video import factory as video_factory
from backend.app.integrations.video.base import VideoGenerationStart, VideoGenerationStatus
from backend.app.main import create_app
from backend.app.services import external_video_generation_service as external_video_service_module
from backend.app.services import generation_task_service as task_module
from backend.app.services import video_service as video_service_module
from backend.app.services.campaign_service import CampaignService
from backend.app.services.external_video_generation_service import ExternalVideoGenerationService
from backend.app.services.generation_task_service import VIDEO_QUEUE_NAME, GenerationTaskService
from backend.app.services.video_service import VideoService

EXTERNAL_SOURCE = "external_video_generation"
ONE_PIXEL_PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def external_video_generation_env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("VIDEO_PROVIDER", "placeholder")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ai.example.test")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _session_factory(tmp_path, filename: str = "external-video-generation.db"):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / filename).as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _client_with_db(tmp_path, monkeypatch: pytest.MonkeyPatch, token: str | None = None):
    if token:
        monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", token)
    else:
        monkeypatch.delenv("AI_ADS_ACCESS_TOKEN", raising=False)
    get_settings.cache_clear()

    engine, session_factory = await _session_factory(tmp_path)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    return client, engine, app, session_factory


def _authorized_headers(token: str = "video-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _video_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "external_request_id": "external-video-1",
        "images": [ONE_PIXEL_PNG_BASE64, f"data:image/png;base64,{ONE_PIXEL_PNG_BASE64}"],
        "storyboard_text": "First frame starts on the product, then moves into the final CTA.",
        "duration_seconds": 12,
        "aspect_ratio": "9:16",
    }
    payload.update(overrides)
    return payload


class FakeExternalUrlVideoProvider:
    async def start_generation(self, request):
        return VideoGenerationStart(
            provider_job_id="provider-video-job-external",
            provider_status="queued",
            raw_response={"id": "provider-video-job-external", "status": "queued"},
            request_payload={"prompt": request.prompt},
        )

    async def get_generation_status(self, provider_job_id: str):
        return VideoGenerationStatus(
            provider_job_id=provider_job_id,
            provider_status="succeeded",
            video_url="https://volcengine.example.test/video-output.mp4",
            raw_response={"id": provider_job_id, "status": "succeeded"},
        )


class FailingIfStartedVideoProvider:
    async def start_generation(self, request):
        raise ProviderError("provider start must not run during create request")

    async def get_generation_status(self, provider_job_id: str):
        raise AssertionError("status polling is not part of the create request")


class FakeVideoStorage:
    transfer_calls: list[dict[str, str]] = []

    def __init__(self, settings=None) -> None:
        self.settings = settings or get_settings()

    async def transfer_provider_video(
        self,
        source_url: str,
        video_id: str,
        provider_job_id: str,
    ) -> tuple[str, str]:
        self.transfer_calls.append(
            {
                "source_url": source_url,
                "video_id": video_id,
                "provider_job_id": provider_job_id,
            }
        )
        storage_key = f"local://videos/{video_id}/{provider_job_id}.mp4"
        public_url = self.public_url_for_storage_key(storage_key)
        assert public_url is not None
        return public_url, storage_key

    def public_url_for_storage_key(self, storage_key: str | None) -> str | None:
        if not storage_key or not storage_key.startswith("local://"):
            return None
        relative_path = storage_key.removeprefix("local://").lstrip("/")
        return f"{self.settings.public_base_url.rstrip('/')}/storage/{relative_path}"

    def storage_key_for_public_url(self, url: str | None) -> str | None:
        if not url:
            return None
        prefix = f"{self.settings.public_base_url.rstrip('/')}/storage/"
        if not url.startswith(prefix):
            return None
        return f"local://{url.removeprefix(prefix)}"


class TransientStartVideoService:
    def __init__(self) -> None:
        self.calls = 0

    async def start_video_generation(self, session, video_id: str):
        self.calls += 1
        if self.calls < 3:
            raise ProviderError("Volcengine video API returned 502: Bad Gateway")
        video = await session.get(VideoAsset, video_id)
        assert video is not None
        video.provider_job_id = "provider-video-job-retried"
        video.status = VideoStatus.GENERATING.value
        video.error_message = None
        await session.commit()
        await session.refresh(video)
        return video


class InvalidStartVideoService:
    def __init__(self) -> None:
        self.calls = 0

    async def start_video_generation(self, session, video_id: str):
        self.calls += 1
        raise AppError("duration_seconds is out of range")


def test_volcengine_video_timeout_seconds_is_passed_to_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeVolcengineVideoProvider:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VOLCENGINE_VIDEO_API_KEY", "video-api-key")
    monkeypatch.setenv("VOLCENGINE_VIDEO_TIMEOUT_SECONDS", "123.5")
    get_settings.cache_clear()
    monkeypatch.setattr(video_factory, "VolcengineVideoProvider", FakeVolcengineVideoProvider)

    video_factory.get_video_provider()

    assert captured["timeout_seconds"] == 123.5


@pytest.mark.asyncio
async def test_external_video_generation_create_returns_job_without_starting_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    enqueued: list[tuple[str, str, int]] = []

    def capture_enqueue(
        task_id: str,
        queue_name: str,
        priority: int,
        countdown_seconds: int = 0,
    ) -> None:
        assert countdown_seconds == 0
        enqueued.append((task_id, queue_name, priority))

    monkeypatch.setattr(
        video_service_module,
        "get_video_provider",
        lambda settings: FailingIfStartedVideoProvider(),
    )
    monkeypatch.setattr(
        "backend.app.services.generation_task_dispatcher._enqueue_celery_generation_task",
        capture_enqueue,
    )
    client, engine, app, _session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    try:
        create_response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(),
        )
        create_body = create_response.json()
        created = create_body["data"]

        poll_response = client.get(
            f"/api/v1/integrations/video-generation/jobs/{created['job_id']}",
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
    assert poll_response.status_code == 200
    assert poll_body["code"] == 1001
    assert poll_body["message"] == "processing"
    assert polled["job_id"] == created["job_id"]
    assert polled["status"] == "processing"
    assert "url" not in polled
    assert len(enqueued) == 1
    start_task_id, start_queue_name, _priority = enqueued[0]
    assert start_queue_name == VIDEO_QUEUE_NAME

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        campaigns = (await session.execute(select(Campaign))).scalars().all()
        assets = (await session.execute(select(CreativeAsset))).scalars().all()
        videos = (await session.execute(select(VideoAsset))).scalars().all()
        work_orders = (await session.execute(select(WorkOrder))).scalars().all()
        tasks = (await session.execute(select(GenerationTask))).scalars().all()

    assert len(campaigns) == 1
    assert campaigns[0].work_order_id is None
    assert campaigns[0].metadata_json["source"] == EXTERNAL_SOURCE
    assert campaigns[0].metadata_json["external_request_id"] == "external-video-1"
    assert work_orders == []
    assert len(assets) == 2
    assert [asset.metadata_json["keyframe_role"] for asset in assets] == [
        "first_frame",
        "last_frame",
    ]
    assert len(videos) == 1
    assert videos[0].prompt == _video_payload()["storyboard_text"]
    assert videos[0].storyboard == []
    assert videos[0].status == VideoStatus.REQUESTED.value
    assert videos[0].provider_job_id is None
    assert videos[0].metadata_json["source"] == EXTERNAL_SOURCE
    assert videos[0].metadata_json["external_request_id"] == "external-video-1"
    assert len(tasks) == 1
    assert tasks[0].id == start_task_id
    assert tasks[0].queue_name == VIDEO_QUEUE_NAME
    assert tasks[0].task_type == "external_video_start"
    assert tasks[0].business_type == EXTERNAL_SOURCE
    assert tasks[0].business_id == videos[0].id
    assert tasks[0].payload_json == {"video_id": videos[0].id}
    assert tasks[0].metadata_json["source"] == EXTERNAL_SOURCE
    assert tasks[0].metadata_json["external_request_id"] == "external-video-1"

    await engine.dispose()


@pytest.mark.asyncio
async def test_external_video_generation_start_task_then_polling_returns_url(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    enqueued: list[tuple[str, str, int]] = []

    def capture_enqueue(
        task_id: str,
        queue_name: str,
        priority: int,
        countdown_seconds: int = 0,
    ) -> None:
        assert countdown_seconds == 0
        enqueued.append((task_id, queue_name, priority))

    monkeypatch.setattr(
        "backend.app.services.generation_task_dispatcher._enqueue_celery_generation_task",
        capture_enqueue,
    )
    client, engine, app, session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)
    try:
        create_response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(external_request_id="external-video-start-task"),
        )
        created = create_response.json()["data"]
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert create_response.status_code == 202
    assert len(enqueued) == 1
    start_task_id, start_queue_name, _priority = enqueued[0]
    assert start_queue_name == VIDEO_QUEUE_NAME

    await GenerationTaskService().process_task(start_task_id)

    client, _second_engine, app, _second_session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    try:
        poll_response = client.get(
            f"/api/v1/integrations/video-generation/jobs/{created['job_id']}",
            headers=_authorized_headers(),
        )
        poll_body = poll_response.json()
        polled = poll_body["data"]
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert poll_response.status_code == 200
    assert poll_body["code"] == 0
    assert poll_body["message"] == "success"
    assert polled["job_id"] == created["job_id"]
    assert polled["status"] == "succeeded"
    assert polled["url"].startswith("https://ai.example.test/storage/videos/")

    await engine.dispose()


@pytest.mark.asyncio
async def test_external_video_generation_retries_transient_start_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _engine, session_factory = await _session_factory(tmp_path, "video-start-retry.db")
    fake_video_service = TransientStartVideoService()
    monkeypatch.setattr(
        external_video_service_module,
        "VIDEO_START_RETRY_DELAYS_SECONDS",
        (0, 0),
        raising=False,
    )

    async with session_factory() as session:
        service = ExternalVideoGenerationService()
        service.video_service = fake_video_service

        job, task = await service.create_video(
            session,
            external_video_service_module.ExternalVideoGenerationCreate.model_validate(
                _video_payload(external_request_id="video-start-retry")
            ),
        )
        assert task is not None
        job = external_video_service_module.ExternalVideoGenerationService()._job_read(
            await service.execute_start_task(session, task)
        )

    assert fake_video_service.calls == 3
    assert job.status == "processing"
    assert job.job_id

    await _engine.dispose()


@pytest.mark.asyncio
async def test_external_video_generation_start_task_failure_marks_video_failed(
    tmp_path,
) -> None:
    _engine, session_factory = await _session_factory(tmp_path, "video-start-app-error.db")
    fake_video_service = InvalidStartVideoService()

    async with session_factory() as session:
        service = ExternalVideoGenerationService()
        service.video_service = fake_video_service

        job, task = await service.create_video(
            session,
            external_video_service_module.ExternalVideoGenerationCreate.model_validate(
                _video_payload(external_request_id="video-start-app-error")
            ),
        )
        assert task is not None

        with pytest.raises(AppError, match="duration_seconds"):
            await service.execute_start_task(session, task)

        failed_video = await session.get(VideoAsset, job.job_id)

    assert fake_video_service.calls == 1
    assert failed_video is not None
    assert failed_video.status == VideoStatus.FAILED.value
    assert "duration_seconds" in (failed_video.error_message or "")

    await _engine.dispose()


@pytest.mark.asyncio
async def test_external_video_polling_schedules_transfer_without_downloading_in_request(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    FakeVideoStorage.transfer_calls = []
    enqueued: list[tuple[str, str, int]] = []

    def capture_enqueue(
        task_id: str,
        queue_name: str,
        priority: int,
        countdown_seconds: int = 0,
    ) -> None:
        assert countdown_seconds == 0
        enqueued.append((task_id, queue_name, priority))

    monkeypatch.setattr(
        video_service_module,
        "get_video_provider",
        lambda settings: FakeExternalUrlVideoProvider(),
    )
    monkeypatch.setattr(video_service_module, "VideoStorageService", FakeVideoStorage)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", None)
    monkeypatch.setattr(
        "backend.app.services.generation_task_dispatcher._enqueue_celery_generation_task",
        capture_enqueue,
    )
    client, engine, app, session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)

    try:
        create_response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(external_request_id="video-transfer-outside-get"),
        )
        created = create_response.json()["data"]
        assert len(enqueued) == 1
        start_task_id, start_queue_name, _start_priority = enqueued[0]
        assert start_queue_name == VIDEO_QUEUE_NAME

        await GenerationTaskService().process_task(start_task_id)

        first_poll_response = client.get(
            f"/api/v1/integrations/video-generation/jobs/{created['job_id']}",
            headers=_authorized_headers(),
        )
        first_polled = first_poll_response.json()["data"]
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert create_response.status_code == 202
    assert first_poll_response.status_code == 200
    assert first_polled["status"] == "processing"
    assert "url" not in first_polled
    assert FakeVideoStorage.transfer_calls == []
    assert len(enqueued) == 2
    transfer_task_id, transfer_queue_name, _priority = enqueued[1]
    assert transfer_queue_name == VIDEO_QUEUE_NAME

    async with session_factory() as session:
        start_task = await session.get(GenerationTask, start_task_id)
        transfer_task = await session.get(GenerationTask, transfer_task_id)
        video = await session.get(VideoAsset, created["job_id"])

    assert start_task is not None
    assert start_task.task_type == "external_video_start"
    assert transfer_task is not None
    assert transfer_task.task_type == "video_transfer"
    assert transfer_task.business_id == created["job_id"]
    assert video is not None
    assert video.status == VideoStatus.GENERATED.value
    assert video.url is None
    assert video.metadata_json["implementation_status"] == "pending_transfer"
    assert video.metadata_json["provider_video_url"] == "https://volcengine.example.test/video-output.mp4"

    await GenerationTaskService().process_task(transfer_task_id)

    assert FakeVideoStorage.transfer_calls == [
        {
            "source_url": "https://volcengine.example.test/video-output.mp4",
            "video_id": created["job_id"],
            "provider_job_id": "provider-video-job-external",
        }
    ]

    async with session_factory() as session:
        await VideoService().transfer_completed_video(session, created["job_id"])
        stored_video = await session.get(VideoAsset, created["job_id"])

    assert FakeVideoStorage.transfer_calls == [
        {
            "source_url": "https://volcengine.example.test/video-output.mp4",
            "video_id": created["job_id"],
            "provider_job_id": "provider-video-job-external",
        }
    ]
    assert stored_video is not None
    assert stored_video.url == (
        f"https://ai.example.test/storage/videos/{created['job_id']}/"
        "provider-video-job-external.mp4"
    )
    assert stored_video.metadata_json["implementation_status"] == "transferred"

    client, final_engine, app, _session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    try:
        final_poll_response = client.get(
            f"/api/v1/integrations/video-generation/jobs/{created['job_id']}",
            headers=_authorized_headers(),
        )
        final_polled = final_poll_response.json()["data"]
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert final_poll_response.status_code == 200
    assert final_polled["status"] == "succeeded"
    assert final_polled["url"] == stored_video.url

    await final_engine.dispose()
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_video_generation_rejects_image_counts_other_than_two(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app, _session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    try:
        response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(images=[ONE_PIXEL_PNG_BASE64]),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    assert response.json()["code"] == 4001
    assert "exactly 2" in response.json()["message"]


@pytest.mark.asyncio
async def test_external_video_generation_rejects_structured_storyboard_field(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app, _session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    try:
        response = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=_video_payload(storyboard=[{"scene_index": 1}]),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    assert response.json()["code"] == 4001


@pytest.mark.asyncio
async def test_external_video_generation_requires_storyboard_text(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app, _session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    try:
        payload = _video_payload()
        payload.pop("storyboard_text")
        response = client.post(
            "/api/v1/integrations/video-generation/videos",
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
async def test_external_video_generation_requires_ai_ads_access_token(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app, _session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    try:
        response = client.post(
            "/api/v1/integrations/video-generation/videos",
            json=_video_payload(),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_external_video_generation_reuses_duplicate_external_request_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "celery")
    get_settings.cache_clear()
    enqueued: list[tuple[str, str, int]] = []

    def capture_enqueue(
        task_id: str,
        queue_name: str,
        priority: int,
        countdown_seconds: int = 0,
    ) -> None:
        assert countdown_seconds == 0
        enqueued.append((task_id, queue_name, priority))

    monkeypatch.setattr(
        "backend.app.services.generation_task_dispatcher._enqueue_celery_generation_task",
        capture_enqueue,
    )
    client, engine, app, _session_factory = await _client_with_db(
        tmp_path,
        monkeypatch,
        token="video-token",
    )
    payload = _video_payload(external_request_id="external-video-idempotent")
    try:
        first = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=payload,
        )
        second = client.post(
            "/api/v1/integrations/video-generation/videos",
            headers=_authorized_headers(),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["job_id"] == second.json()["data"]["job_id"]

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        videos = (await session.execute(select(VideoAsset))).scalars().all()
        tasks = (await session.execute(select(GenerationTask))).scalars().all()

    assert len(videos) == 1
    assert len(tasks) == 1
    assert tasks[0].task_type == "external_video_start"
    assert len(enqueued) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_campaign_list_filters_external_generation_placeholders(tmp_path) -> None:
    engine, session_factory = await _session_factory(tmp_path, "campaign-filter.db")
    async with session_factory() as session:
        visible_campaign = Campaign(name="Visible campaign", metadata_json={})
        video_placeholder = Campaign(
            name="External video placeholder",
            work_order_id=None,
            metadata_json={"source": EXTERNAL_SOURCE},
        )
        material_placeholder = Campaign(
            name="External material placeholder",
            work_order_id=None,
            metadata_json={"source": "external_material_generation"},
        )
        session.add_all([visible_campaign, video_placeholder, material_placeholder])
        await session.commit()

        campaigns = await CampaignService().list_campaigns(session, limit=10, offset=0)

    assert [campaign.id for campaign in campaigns] == [visible_campaign.id]
    await engine.dispose()
