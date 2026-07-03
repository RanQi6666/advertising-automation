import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.video_asset import VideoAsset
from backend.app.db.models.work_order import WorkOrder
from backend.app.db.session import get_session
from backend.app.main import create_app
from backend.app.services.campaign_service import CampaignService

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
    return client, engine, app


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


@pytest.mark.asyncio
async def test_external_video_generation_creates_async_job_and_polling_returns_url(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="video-token")
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
    assert poll_body["code"] == 0
    assert poll_body["message"] == "success"
    assert polled["job_id"] == created["job_id"]
    assert polled["status"] == "succeeded"
    assert polled["url"].startswith("https://ai.example.test/storage/videos/")

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        campaigns = (await session.execute(select(Campaign))).scalars().all()
        assets = (await session.execute(select(CreativeAsset))).scalars().all()
        videos = (await session.execute(select(VideoAsset))).scalars().all()
        work_orders = (await session.execute(select(WorkOrder))).scalars().all()

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
    assert videos[0].metadata_json["source"] == EXTERNAL_SOURCE
    assert videos[0].metadata_json["external_request_id"] == "external-video-1"

    await engine.dispose()


@pytest.mark.asyncio
async def test_external_video_generation_rejects_image_counts_other_than_two(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="video-token")
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
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="video-token")
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
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="video-token")
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
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="video-token")
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
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="video-token")
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

    assert len(videos) == 1
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
