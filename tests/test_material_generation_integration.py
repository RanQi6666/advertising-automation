from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.api.v1.endpoints import material_generation as material_generation_endpoint
from backend.app.core.config import get_settings
from backend.app.core.errors import AppError, ProviderError
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.enums import VideoStatus
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.db.session import get_session
from backend.app.integrations.video.placeholder_provider import PlaceholderVideoProvider
from backend.app.main import create_app
from backend.app.schemas.ai import CopyDraftCandidate
from backend.app.schemas.material_generation import MaterialVideoGenerateRequest
from backend.app.services.creative_service import CreativeService
from backend.app.services.material_generation_service import MaterialGenerationService
from backend.app.services.video_service import VideoService


@pytest.fixture(autouse=True)
def mock_material_generation_providers(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("IMAGE_PROVIDER", "placeholder")
    monkeypatch.setenv("VIDEO_PROVIDER", "placeholder")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://ai.example.test")
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _session_factory(tmp_path, filename: str = "material-generation.db"):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / filename).as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _authorized_headers(token: str = "material-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


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
    return client, engine, app


async def _insert_video_job(
    engine,
    *,
    external_request_id: str,
    status: str,
    url: str | None = None,
    error_message: str | None = None,
) -> str:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        campaign = Campaign(
            name="Demo App",
            product_name="Demo App",
            metadata_json={
                "source": "external_material_generation",
                "external_request_id": external_request_id,
                "material_type": "video",
            },
        )
        session.add(campaign)
        await session.flush()

        topic = ContentTopic(
            campaign_id=campaign.id,
            title="Demo App",
            angle="Create a short video for daily use.",
            source_data={
                "source": "external_material_generation",
                "external_request_id": external_request_id,
                "material_type": "video",
            },
        )
        session.add(topic)
        await session.flush()

        draft = CopyDraft(
            campaign_id=campaign.id,
            topic_id=topic.id,
            body="Create a short video for daily use.",
            primary_text="Create a short video for daily use.",
            headline="Demo App",
            cta="Learn More",
            model_name="test",
            prompt_version="test",
            metadata_json={
                "source": "external_material_generation",
                "external_request_id": external_request_id,
                "material_type": "video",
            },
        )
        session.add(draft)
        await session.flush()

        video = VideoAsset(
            campaign_id=campaign.id,
            draft_id=draft.id,
            source_asset_ids=[],
            status=status,
            url=url,
            error_message=error_message,
            metadata_json={
                "source": "external_material_generation",
                "external_request_id": external_request_id,
                "material_type": "video",
            },
        )
        session.add(video)
        await session.commit()
        return video.id


@pytest.mark.asyncio
async def test_material_generation_auth_failure_uses_external_error_body(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            json={"product_name": "Demo App", "brief": "Create a clear daily-use ad."},
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 401
    assert response.json() == {
        "code": 4003,
        "message": "invalid or missing access token",
        "data": {},
    }


@pytest.mark.asyncio
async def test_material_generation_validation_error_uses_external_error_body(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={"brief": "Create a clear daily-use ad."},
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    assert response.json()["code"] == 4001
    assert response.json()["message"] == "product_name is required"
    assert response.json()["data"] == {}


@pytest.mark.asyncio
async def test_material_generation_request_validation_error_uses_external_error_body(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={
                "product_name": "Demo App",
                "brief": "Create a clear daily-use ad.",
                "landing_url": "not-a-url",
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == 4001
    assert body["message"] == "request validation failed"
    assert body["data"]["errors"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("endpoint", "payload", "expected_status"),
    (
        (
            "/api/v1/integrations/material-generation/copy",
            {
                "product_name": "Demo App",
                "brief": "Create a free discount ad with low price language.",
            },
            409,
        ),
        (
            "/api/v1/integrations/material-generation/images",
            {
                "product_name": "Demo App",
                "brief": "Create a free discount visual with low price language.",
            },
            409,
        ),
        (
            "/api/v1/integrations/material-generation/videos",
            {
                "product_name": "Demo App",
                "brief": "Create a free discount video with low price language.",
                "image_urls": ["https://ai.example.test/storage/images/source.svg"],
            },
            409,
        ),
    ),
)
async def test_material_generation_blocks_brand_safety_risky_input(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
    payload: dict[str, object],
    expected_status: int,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(endpoint, headers=_authorized_headers(), json=payload)
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == expected_status
    assert response.json()["code"] == 4091
    assert response.json()["message"] == "brand safety check failed"
    assert response.json()["data"]["brand_safety"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_material_generation_accepts_ai_access_token_query(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy?ai_access_token=material-token",
            json={
                "product_name": "Demo App",
                "brief": "Create a clear daily-use ad.",
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 200
    assert response.json()["code"] == 0


@pytest.mark.asyncio
async def test_material_generation_provider_error_uses_external_error_body(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ProviderErrorService:
        async def generate_copy(self, _session, _payload):
            raise ProviderError("provider unavailable")

    monkeypatch.setattr(
        material_generation_endpoint,
        "_material_generation_service",
        ProviderErrorService,
    )
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={
                "product_name": "Demo App",
                "brief": "Create a clear daily-use ad.",
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 500
    assert response.json() == {
        "code": 5001,
        "message": "provider unavailable",
        "data": {},
    }


@pytest.mark.asyncio
async def test_material_generation_app_error_uses_external_error_body(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AppErrorService:
        async def generate_copy(self, _session, _payload):
            raise AppError("bad app state")

    monkeypatch.setattr(
        material_generation_endpoint,
        "_material_generation_service",
        AppErrorService,
    )
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={
                "product_name": "Demo App",
                "brief": "Create a clear daily-use ad.",
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    assert response.json() == {
        "code": 4001,
        "message": "bad app state",
        "data": {},
    }


@pytest.mark.asyncio
async def test_create_app_uses_current_local_storage_root_env(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    marker = storage_root / "marker.txt"
    marker.write_text("current storage root", encoding="utf-8")

    client, engine, app = await _client_with_db(tmp_path, monkeypatch)
    try:
        response = client.get("/storage/marker.txt")
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 200
    assert response.text == "current storage root"


@pytest.mark.asyncio
async def test_material_copy_generation_returns_copy_and_stores_external_context(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={
                "external_request_id": "copy-ext-1",
                "product_name": "Demo App",
                "landing_url": "https://example.com/demo",
                "audience": "New users who need a simple setup",
                "country": "US",
                "event_name": "quick registration",
                "brief": "Create a clear ad for daily use.",
                "selling_points": ["Simple setup", "Clear content"],
            },
        )
        body = response.json()
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 200
    assert body["code"] == 0
    assert body["message"] == "success"
    assert body["data"]["request_id"]
    assert body["data"]["primary_text"]
    assert body["data"]["headline"]
    assert body["data"]["cta"]
    assert body["data"]["customEventType"] == "COMPLETE_REGISTRATION"

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        campaigns = (await session.execute(select(Campaign))).scalars().all()
        topics = (await session.execute(select(ContentTopic))).scalars().all()
        drafts = (await session.execute(select(CopyDraft))).scalars().all()

    assert len(campaigns) == 1
    assert len(topics) == 1
    assert len(drafts) == 1
    assert campaigns[0].metadata_json["source"] == "external_material_generation"
    assert campaigns[0].metadata_json["external_request_id"] == "copy-ext-1"

    await engine.dispose()


@pytest.mark.asyncio
async def test_material_copy_generation_reuses_successful_external_request_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    payload = {
        "external_request_id": "copy-ext-idempotent",
        "product_name": "Demo App",
        "brief": "Create a clear ad for daily use.",
    }
    try:
        first = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json=payload,
        )
        second = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["request_id"] == second.json()["data"]["request_id"]

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        drafts = (await session.execute(select(CopyDraft))).scalars().all()

    assert len(drafts) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_material_copy_generation_rolls_back_when_brand_safety_blocks_output(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = MaterialGenerationService()

    class RiskyCopyProvider:
        async def generate_copy(self, **_kwargs):
            return CopyDraftCandidate(
                body="Free cash casino jackpot",
                primary_text="Free cash casino jackpot",
                headline="Free casino cash",
                description="Claim free cash now",
                cta="Learn More",
            )

    monkeypatch.setattr(
        service.copywriting,
        "llm_for_model",
        lambda _model_id: (RiskyCopyProvider(), service.copywriting.settings),
    )
    monkeypatch.setattr(
        material_generation_endpoint,
        "_material_generation_service",
        lambda: service,
    )
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={
                "external_request_id": "copy-ext-blocked",
                "product_name": "Demo App",
                "brief": "Create a clear ad for daily use.",
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 409
    assert response.json()["code"] == 4091

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        campaigns = (await session.execute(select(Campaign))).scalars().all()
        topics = (await session.execute(select(ContentTopic))).scalars().all()
        drafts = (await session.execute(select(CopyDraft))).scalars().all()

    assert campaigns == []
    assert topics == []
    assert drafts == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_material_copy_generation_replay_keeps_original_custom_event_type(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        first = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={
                "external_request_id": "copy-ext-event-snapshot",
                "product_name": "Demo App",
                "event_name": "quick registration",
                "brief": "Create a clear ad for daily use.",
            },
        )
        second = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={
                "external_request_id": "copy-ext-event-snapshot",
                "product_name": "Demo App",
                "event_name": "purchase",
                "brief": "Create a clear ad for daily use.",
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["request_id"] == second.json()["data"]["request_id"]
    assert first.json()["data"]["customEventType"] == "COMPLETE_REGISTRATION"
    assert second.json()["data"]["customEventType"] == "COMPLETE_REGISTRATION"
    await engine.dispose()


@pytest.mark.asyncio
async def test_material_copy_generation_does_not_initialize_image_provider(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IMAGE_PROVIDER", "volcengine")
    monkeypatch.delenv("VOLCENGINE_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    get_settings.cache_clear()

    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/copy",
            headers=_authorized_headers(),
            json={
                "external_request_id": "copy-ext-no-image-provider",
                "product_name": "Demo App",
                "brief": "Create a clear ad for daily use.",
            },
        )
        body = response.json()
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 200
    assert body["code"] == 0
    assert body["data"]["request_id"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_material_image_generation_returns_public_urls(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/images",
            headers=_authorized_headers(),
            json={
                "external_request_id": "image-ext-1",
                "product_name": "Demo App",
                "brief": "Create a clean product visual for daily use.",
                "count": 2,
                "size": "1:1",
            },
        )
        body = response.json()
        stored_urls = body["data"]["urls"]
        storage_responses = [
            client.get(urlparse(url).path) for url in stored_urls
        ]
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 200
    assert body["code"] == 0
    assert body["message"] == "success"
    assert body["data"]["request_id"]
    assert len(body["data"]["urls"]) == 2
    assert all(url.startswith("https://ai.example.test/storage/") for url in body["data"]["urls"])
    assert all(storage_response.status_code == 200 for storage_response in storage_responses)
    assert all(storage_response.text.startswith("<svg ") for storage_response in storage_responses)

    storage_root = tmp_path / "storage"
    for url in stored_urls:
        storage_path = urlparse(url).path.removeprefix("/storage/")
        assert (storage_root / storage_path).is_file()

    await engine.dispose()


@pytest.mark.asyncio
async def test_material_image_generation_reuses_external_request_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    payload = {
        "external_request_id": "image-ext-idempotent",
        "product_name": "Demo App",
        "brief": "Create a clean product visual for daily use.",
        "count": 2,
        "size": "1:1",
    }
    try:
        first = client.post(
            "/api/v1/integrations/material-generation/images",
            headers=_authorized_headers(),
            json=payload,
        )
        second = client.post(
            "/api/v1/integrations/material-generation/images",
            headers=_authorized_headers(),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["data"]["request_id"] == second.json()["data"]["request_id"]
    assert first.json()["data"]["urls"] == second.json()["data"]["urls"]

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        assets = (await session.execute(select(CreativeAsset))).scalars().all()

    assert len(assets) == 2
    await engine.dispose()


@pytest.mark.asyncio
async def test_material_image_generation_rolls_back_when_brand_safety_blocks_asset(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_asset_builder = CreativeService._asset_from_generated_image

    async def risky_asset_builder(self, *args, **kwargs):
        asset = await original_asset_builder(self, *args, **kwargs)
        asset.prompt = "casino cash jackpot"
        asset.alt_text = "casino cash jackpot"
        return asset

    monkeypatch.setattr(
        CreativeService,
        "_asset_from_generated_image",
        risky_asset_builder,
    )
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/images",
            headers=_authorized_headers(),
            json={
                "external_request_id": "image-ext-blocked",
                "product_name": "Demo App",
                "brief": "Create a clean product visual for daily use.",
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 409
    assert response.json()["code"] == 4091

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        assets = (await session.execute(select(CreativeAsset))).scalars().all()
        drafts = (await session.execute(select(CopyDraft))).scalars().all()

    assert assets == []
    assert len(drafts) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_material_video_generation_creates_async_job_and_query_returns_url(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        image_response = client.post(
            "/api/v1/integrations/material-generation/images",
            headers=_authorized_headers(),
            json={
                "external_request_id": "video-image-ext-1",
                "product_name": "Demo App",
                "brief": "Create a clean product visual for daily use.",
                "count": 1,
            },
        )
        image_url = image_response.json()["data"]["urls"][0]

        create_response = client.post(
            "/api/v1/integrations/material-generation/videos",
            headers=_authorized_headers(),
            json={
                "external_request_id": "video-ext-1",
                "product_name": "Demo App",
                "brief": "Create a short video for daily use.",
                "image_urls": [image_url],
                "duration_seconds": 6,
                "aspect_ratio": "9:16",
                "prompt": "Use a simple motion sequence.",
            },
        )
        create_body = create_response.json()
        status_response = client.get(
            f"/api/v1/integrations/material-generation/jobs/{create_body['data']['job_id']}",
            headers=_authorized_headers(),
        )
        status_body = status_response.json()
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert create_response.status_code == 202
    assert create_body["code"] == 1001
    assert create_body["message"] == "processing"
    assert create_body["data"]["job_id"]
    assert create_body["data"]["status"] == "processing"

    assert status_response.status_code == 200
    assert status_body["code"] == 0
    assert status_body["message"] == "success"
    assert status_body["data"]["status"] == "succeeded"
    assert status_body["data"]["url"].startswith("https://ai.example.test/storage/")

    await engine.dispose()


@pytest.mark.asyncio
async def test_material_video_generation_requires_reference_image(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/videos",
            headers=_authorized_headers(),
            json={
                "product_name": "Demo App",
                "brief": "Create a short video for daily use.",
                "image_urls": [],
            },
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    assert response.json()["code"] == 4001
    assert response.json()["message"] == "image_urls is required"


@pytest.mark.asyncio
async def test_material_video_generation_rolls_back_when_provider_start_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_start_generation(self, request):
        raise ProviderError("video provider unavailable")

    monkeypatch.setattr(
        PlaceholderVideoProvider,
        "start_generation",
        fail_start_generation,
    )
    engine, session_factory = await _session_factory(tmp_path)
    service = MaterialGenerationService()
    payload = MaterialVideoGenerateRequest(
        external_request_id="video-ext-provider-fail",
        product_name="Demo App",
        brief="Create a short video for daily use.",
        image_urls=["https://ai.example.test/storage/images/source.svg"],
    )

    async with session_factory() as session:
        with pytest.raises(ProviderError):
            await service.create_video(session, payload)

    async with session_factory() as session:
        campaigns = (await session.execute(select(Campaign))).scalars().all()
        topics = (await session.execute(select(ContentTopic))).scalars().all()
        drafts = (await session.execute(select(CopyDraft))).scalars().all()
        assets = (await session.execute(select(CreativeAsset))).scalars().all()
        videos = (await session.execute(select(VideoAsset))).scalars().all()

    assert campaigns == []
    assert topics == []
    assert drafts == []
    assert assets == []
    assert videos == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_material_video_generation_reuses_duplicate_external_request_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    payload = {
        "external_request_id": "video-ext-idempotent",
        "product_name": "Demo App",
        "brief": "Create a short video for daily use.",
        "image_urls": ["https://ai.example.test/storage/images/source.svg"],
    }
    try:
        first = client.post(
            "/api/v1/integrations/material-generation/videos",
            headers=_authorized_headers(),
            json=payload,
        )
        second = client.post(
            "/api/v1/integrations/material-generation/videos",
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

    assert len(videos) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_material_video_generation_reuses_requested_external_request_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    existing_job_id = await _insert_video_job(
        engine,
        external_request_id="video-ext-requested",
        status=VideoStatus.REQUESTED.value,
    )
    try:
        response = client.post(
            "/api/v1/integrations/material-generation/videos",
            headers=_authorized_headers(),
            json={
                "external_request_id": "video-ext-requested",
                "product_name": "Demo App",
                "brief": "Create a short video for daily use.",
                "image_urls": ["https://ai.example.test/storage/images/source.svg"],
            },
        )
        body = response.json()
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 202
    assert body["code"] == 1001
    assert body["message"] == "processing"
    assert body["data"]["job_id"] == existing_job_id

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        videos = (await session.execute(select(VideoAsset))).scalars().all()

    assert len(videos) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_material_video_job_missing_returns_external_404_envelope(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    try:
        response = client.get(
            "/api/v1/integrations/material-generation/jobs/missing-job-id",
            headers=_authorized_headers(),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 404
    assert response.json() == {
        "code": 4001,
        "message": "video job not found",
        "data": {},
    }


@pytest.mark.asyncio
async def test_material_video_job_failed_returns_provider_error_envelope(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="material-token")
    failed_job_id = await _insert_video_job(
        engine,
        external_request_id="video-ext-failed",
        status=VideoStatus.FAILED.value,
        error_message="boom",
    )
    try:
        response = client.get(
            f"/api/v1/integrations/material-generation/jobs/{failed_job_id}",
            headers=_authorized_headers(),
        )
        body = response.json()
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 200
    assert body["code"] == 5001
    assert body["message"] == "video generation failed"
    assert body["data"] == {
        "job_id": failed_job_id,
        "status": "failed",
        "error": "boom",
    }


def test_video_service_does_not_initialize_video_provider_until_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.delenv("VOLCENGINE_VIDEO_API_KEY", raising=False)
    monkeypatch.delenv("VOLCENGINE_API_KEY", raising=False)
    monkeypatch.delenv("ARK_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        service = VideoService()
    finally:
        get_settings.cache_clear()

    assert service.settings.video_provider == "volcengine"
