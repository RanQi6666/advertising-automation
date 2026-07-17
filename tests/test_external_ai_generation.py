import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.services.generation_task_service as task_module
from backend.app.core.config import get_settings
from backend.app.db.base import Base
from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.generation_task import GenerationTask
from backend.app.db.models.topic import ContentTopic
from backend.app.db.models.video_asset import VideoAsset
from backend.app.db.models.work_order import WorkOrder
from backend.app.db.session import get_session
from backend.app.integrations.llm.mock_provider import MockLLMProvider
from backend.app.main import create_app
from backend.app.schemas.ai import (
    CopyDraftCandidate,
    TopicCandidate,
    VideoStoryboardCandidate,
    VideoStoryboardScene,
)
from backend.app.schemas.external_ai_generation import (
    ExternalAIFrameAnchoredStoryboardCreate,
)
from backend.app.services import external_ai_generation_service as external_ai_service_module
from backend.app.services.external_ai_generation_service import ExternalAIGenerationService


@pytest.fixture(autouse=True)
def external_ai_generation_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.app.services import llm_rate_limit

    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("GENERATION_TASK_EXECUTION_BACKEND", "background_tasks")
    get_settings.cache_clear()
    llm_rate_limit.set_redis_client_factory_for_tests(lambda _url: FakeRedis())
    yield
    llm_rate_limit.set_redis_client_factory_for_tests(None)
    get_settings.cache_clear()


async def _session_factory(tmp_path, filename: str = "external-ai-generation.db"):
    database_url = f"sqlite+aiosqlite:///{(tmp_path / filename).as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _client_with_db(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    token: str | None = "ai-token",
    filename: str = "external-ai-generation.db",
):
    if token:
        monkeypatch.setenv("AI_ADS_ACCESS_TOKEN", token)
    else:
        monkeypatch.delenv("AI_ADS_ACCESS_TOKEN", raising=False)
    get_settings.cache_clear()

    engine, session_factory = await _session_factory(tmp_path, filename)
    monkeypatch.setattr(task_module, "AsyncSessionLocal", session_factory)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)
    return client, engine, app


def _authorized_headers(token: str = "ai-token") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class RecordingLimiter:
    def __init__(self) -> None:
        self.entered = 0
        self.exited = 0

    async def __aenter__(self):
        self.entered += 1
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback
        self.exited += 1


class FakeExternalAILLM:
    async def generate_topics(self, campaign, limit: int, signals: dict):
        del campaign, signals
        return [
            TopicCandidate(
                title=f"Topic {index}",
                angle="Benefit-led angle",
                angle_type="benefit",
                audience="Audience",
                selling_points=["Point"],
                rationale="Reason",
            )
            for index in range(1, limit + 1)
        ]

    async def generate_copy(self, campaign, topic, constraints: dict):
        del campaign, topic
        return CopyDraftCandidate(
            body="Primary text",
            primary_text="Primary text",
            headline="Headline",
            description="Description",
            cta=constraints.get("cta", "LEARN_MORE"),
        )

    async def generate_video_storyboard(
        self,
        campaign,
        draft,
        assets,
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        instructions: str | None = None,
    ):
        del campaign, draft, assets, context, instructions
        return VideoStoryboardCandidate(
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            scenes=[
                VideoStoryboardScene(
                    scene_index=1,
                    visual="Show product benefit.",
                    subtitle="Start now",
                    motion="Slow push-in",
                    voiceover="Discover the product.",
                )
            ],
        )


class FakeWorkOrderExtraction:
    def model_dump(self, mode: str = "json") -> dict:
        del mode
        return {
            "fields": {
                "landing_url": {
                    "value": "https://example.com/page.html",
                    "normalized_value": "https://example.com/page.html",
                    "confidence": 1,
                },
                "event_name": {
                    "value": "purchase",
                    "normalized_value": "purchase",
                    "confidence": 1,
                },
                "country": {"value": "IN", "normalized_value": "IN", "confidence": 1},
            },
            "review": {},
        }


class FakeWorkOrderExtractor:
    async def extract_delivery_fields(self, raw_content: str):
        assert raw_content
        return FakeWorkOrderExtraction()


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.set_calls = 0
        self.get_calls = 0

    async def set(self, key: str, value: str, nx: bool = False, px=None, ex=None):
        del px, ex
        if nx and key in self.values:
            return None
        self.values[key] = value
        self.set_calls += 1
        return True

    async def get(self, key: str):
        self.get_calls += 1
        return self.values.get(key)

    async def eval(self, script: str, numkeys: int, *args):
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        if "redis.call('GET', lock_key)" in script or 'redis.call("GET", lock_key)' in script:
            key = keys[0]
            expected = str(argv[0])
            if self.values.get(key) == expected:
                self.values.pop(key, None)
                return 1
        if "DECR" in script:
            return 0
        return [1, 0, 1, 1]

    async def delete(self, key: str):
        self.values.pop(key, None)
        return 1


def _work_order_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "external_request_id": "external-ai-work-order-1",
        "work_order_type": "ecommerce",
        "work_order_text": (
            "项目名称：Explore India TV\n"
            "投放国家：印度\n"
            "投放媒体：fb\n"
            "投放事件：购买\n"
            "投放人群：年龄25-45\n"
            "投放链接：https://example.com/page.html"
        ),
        "media": "fb",
        "language": "en",
    }
    payload.update(overrides)
    return payload


def _topic_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "external_request_id": "external-ai-topic-1",
        "product_name": "Explore India TV",
        "brief": "Create Facebook ad topics for a home entertainment product.",
        "country": "IN",
        "work_order_type": "ecommerce",
        "count": 2,
        "language": "en",
        "campaign": {"name": "Explore India TV", "objective": "OUTCOME_SALES"},
        "adset": {"countries": "IN", "age_min": 25, "age_max": 45},
        "creative": {"type": "image", "link": "https://example.com/page.html"},
    }
    payload.update(overrides)
    return payload


def _copy_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "external_request_id": "external-ai-copy-1",
        "product_name": "Explore India TV",
        "landing_url": "https://example.com/page.html",
        "audience": "Men aged 25-45 who want easy entertainment.",
        "country": "IN",
        "event_name": "PURCHASE",
        "customEventType": "PURCHASE",
        "language": "en",
        "brief": "Generate direct Facebook ad copy.",
        "selling_points": ["Premium channels", "Simple setup"],
        "count": 2,
        "creative": {"btn_type": "SHOP_NOW", "link": "https://example.com/page.html"},
    }
    payload.update(overrides)
    return payload


def _storyboard_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "external_request_id": "external-ai-storyboard-1",
        "product_name": "Explore India TV",
        "brief": "Show the product benefit quickly and end with a CTA.",
        "image_urls": ["https://cdn.example.test/image-1.jpg"],
        "duration_seconds": 12,
        "aspect_ratio": "9:16",
        "prompt": "Premium vertical social ad storyboard.",
        "language": "zh",
    }
    payload.update(overrides)
    return payload


def _storyboard_v2_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "external_request_id": "external-ai-storyboard-v2-1",
        "first_frame_image_url": "https://cdn.example.test/first.png",
        "last_frame_image_url": "https://cdn.example.test/last.png",
        "duration_seconds": 12,
        "aspect_ratio": "9:16",
    }
    payload.update(overrides)
    return payload


def test_external_storyboard_v2_rejects_legacy_and_missing_frame_fields() -> None:
    request = ExternalAIFrameAnchoredStoryboardCreate.model_validate(
        _storyboard_v2_payload()
    )

    assert request.first_frame_image_url.endswith("first.png")

    with pytest.raises(ValidationError):
        ExternalAIFrameAnchoredStoryboardCreate.model_validate(
            _storyboard_v2_payload(first_frame_image_url="")
        )

    with pytest.raises(ValidationError):
        ExternalAIFrameAnchoredStoryboardCreate.model_validate(
            _storyboard_v2_payload(brief="legacy input")
        )


@pytest.mark.asyncio
async def test_mock_provider_returns_frame_anchored_storyboard() -> None:
    provider = MockLLMProvider()
    analysis = await provider.analyze_video_frame_pair(
        first_frame_image_url="https://cdn.example.test/first.png",
        last_frame_image_url="https://cdn.example.test/last.png",
        duration_seconds=12,
        aspect_ratio="9:16",
    )
    storyboard = await provider.generate_frame_anchored_video_storyboard(
        first_frame_image_url="https://cdn.example.test/first.png",
        last_frame_image_url="https://cdn.example.test/last.png",
        frame_analysis=analysis,
        duration_seconds=12,
        aspect_ratio="9:16",
    )

    assert storyboard.scenes[0].frame_anchor == "first_frame"
    assert storyboard.scenes[1].frame_anchor == "transition"
    assert storyboard.scenes[-1].frame_anchor == "last_frame"
    assert storyboard.sound_design.music
    assert storyboard.sound_design.ambience


async def _count_rows(engine, model) -> int:
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        return int(await session.scalar(select(func.count()).select_from(model)) or 0)


@pytest.mark.asyncio
async def test_external_ai_generation_creates_four_async_jobs_and_polling_returns_results(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch)
    requests = [
        (
            "/api/v1/integrations/ai/work-order-analysis",
            _work_order_payload(),
            "campaign_payload",
        ),
        ("/api/v1/integrations/ai/topics", _topic_payload(), "topics"),
        ("/api/v1/integrations/ai/copy", _copy_payload(), "copywritings"),
        ("/api/v1/integrations/ai/storyboard", _storyboard_payload(), "storyboard_text"),
    ]
    try:
        created_jobs: list[tuple[str, str]] = []
        polled_results: dict[str, dict] = {}
        for url, payload, result_key in requests:
            create_response = client.post(url, headers=_authorized_headers(), json=payload)
            create_body = create_response.json()

            assert create_response.status_code == 202
            assert create_body["code"] == 1001
            assert create_body["message"] == "processing"
            assert create_body["data"]["status"] == "processing"
            assert create_body["data"]["asy_task_id"] == create_body["data"]["job_id"]
            assert create_body["data"]["retry_after_seconds"] == 2

            job_id = create_body["data"]["job_id"]
            created_jobs.append((job_id, result_key))
            poll_response = client.get(
                f"/api/v1/integrations/ai/jobs/{job_id}",
                headers=_authorized_headers(),
            )
            poll_body = poll_response.json()

            assert poll_response.status_code == 200
            assert poll_body["code"] == 0
            assert poll_body["message"] == "success"
            assert poll_body["data"]["job_id"] == job_id
            assert poll_body["data"]["asy_task_id"] == job_id
            assert poll_body["data"]["status"] == "succeeded"
            assert result_key in poll_body["data"]
            polled_results[result_key] = poll_body["data"]
    finally:
        app.dependency_overrides.clear()
        client.close()

    work_order = polled_results["campaign_payload"]
    assert work_order["campaign_payload"]["objective"] == "OUTCOME_SALES"
    assert work_order["campaign_payload"]["status"] == "PAUSED"
    assert work_order["adset_payload"]["optimization_goal"] == "OFFSITE_CONVERSIONS"
    assert work_order["adset_payload"]["customEventType"] == "PURCHASE"
    assert work_order["adset_payload"]["countries"] == "IN"
    assert work_order["creative_payload"]["type"] == "image"
    assert "missing_fields" in work_order["review"]
    assert "warnings" in work_order["review"]
    assert "low_confidence_fields" in work_order["review"]

    topics = polled_results["topics"]["topics"]
    assert len(topics) == 2
    assert {"title", "angle", "angle_type", "audience", "selling_points", "rationale"} <= set(
        topics[0]
    )

    copywritings = polled_results["copywritings"]["copywritings"]
    assert len(copywritings) == 2
    assert {"primary_text", "headline", "description", "cta", "call_to_action"} <= set(
        copywritings[0]
    )
    assert copywritings[0]["customEventType"] == "PURCHASE"
    assert copywritings[0]["call_to_action"] == "SHOP_NOW"

    storyboard = polled_results["storyboard_text"]
    assert storyboard["request_id"]
    assert storyboard["duration_seconds"] == 12
    assert storyboard["aspect_ratio"] == "9:16"
    assert "第1幕" in storyboard["storyboard_text"]
    assert "画面：" in storyboard["storyboard_text"]
    assert "字幕：" in storyboard["storyboard_text"]
    assert "镜头：" in storyboard["storyboard_text"]
    assert "旁白：" in storyboard["storyboard_text"]

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        tasks = (await session.execute(select(GenerationTask))).scalars().all()
    assert {task.queue_name for task in tasks} == {"text_queue"}
    assert {task.task_type for task in tasks} == {
        "external_work_order_analysis",
        "external_topic_selection",
        "external_copy_generation",
        "external_video_storyboard",
    }
    assert {task.business_type for task in tasks} == {"external_ai"}
    assert all(task.campaign_id is None for task in tasks)
    assert {task.id for task in tasks} == {job_id for job_id, _ in created_jobs}

    assert await _count_rows(engine, Campaign) == 0
    assert await _count_rows(engine, WorkOrder) == 0
    assert await _count_rows(engine, ContentTopic) == 0
    assert await _count_rows(engine, CopyDraft) == 0
    assert await _count_rows(engine, CreativeAsset) == 0
    assert await _count_rows(engine, VideoAsset) == 0
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "payload"),
    [
        (
            "/api/v1/integrations/ai/work-order-analysis",
            {"work_order_type": "ecommerce", "work_order_text": "项目名称：A"},
        ),
        ("/api/v1/integrations/ai/topics", {"brief": "missing product"}),
        ("/api/v1/integrations/ai/copy", {"brief": "missing product"}),
        ("/api/v1/integrations/ai/storyboard", {"brief": "missing product"}),
    ],
)
async def test_external_ai_generation_validation_errors_use_4001(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    payload: dict[str, object],
) -> None:
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename=f"{url.rsplit('/', 1)[-1]}-validation.db",
    )
    try:
        response = client.post(url, headers=_authorized_headers(), json=payload)
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    assert response.json()["code"] == 4001


@pytest.mark.asyncio
async def test_external_ai_generation_reuses_duplicate_external_request_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="external-ai-idempotency.db",
    )
    payload = _copy_payload(external_request_id="external-ai-idempotent")
    try:
        first = client.post(
            "/api/v1/integrations/ai/copy",
            headers=_authorized_headers(),
            json=payload,
        )
        second = client.post(
            "/api/v1/integrations/ai/copy",
            headers=_authorized_headers(),
            json=payload,
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["data"]["job_id"] == second.json()["data"]["job_id"]
    assert first.json()["data"]["asy_task_id"] == second.json()["data"]["asy_task_id"]
    assert await _count_rows(engine, GenerationTask) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_ai_generation_requires_ai_ads_access_token(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(tmp_path, monkeypatch, token="ai-token")
    try:
        response = client.post(
            "/api/v1/integrations/ai/copy",
            json=_copy_payload(external_request_id="missing-token"),
        )
        accepted = client.post(
            "/api/v1/integrations/ai/copy?ai_access_token=ai-token",
            json=_copy_payload(external_request_id="query-token"),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 401
    assert accepted.status_code == 202
    assert accepted.json()["code"] == 1001


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_type", "payload"),
    [
        ("external_work_order_analysis", _work_order_payload()),
        ("external_topic_selection", _topic_payload(count=1)),
        ("external_copy_generation", _copy_payload(count=1)),
        ("external_video_storyboard", _storyboard_payload()),
    ],
)
async def test_external_ai_execute_branches_enter_llm_rate_limiter(
    monkeypatch: pytest.MonkeyPatch,
    task_type: str,
    payload: dict[str, object],
) -> None:
    limiter = RecordingLimiter()
    service = ExternalAIGenerationService()
    service.work_orders = FakeWorkOrderExtractor()
    monkeypatch.setattr(
        external_ai_service_module,
        "llm_text_rate_limiter",
        lambda: limiter,
        raising=False,
    )
    monkeypatch.setattr(
        external_ai_service_module,
        "get_llm_provider",
        lambda: FakeExternalAILLM(),
    )
    task = GenerationTask(
        queue_name="text_queue",
        task_type=task_type,
        business_type="external_ai",
        business_id=f"{task_type}-business",
        payload_json=payload,
    )

    await service.execute_task(None, task)  # type: ignore[arg-type]

    assert limiter.entered == 1
    assert limiter.exited == 1


@pytest.mark.asyncio
async def test_external_ai_polling_reads_terminal_result_from_cache_when_db_misses(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.app.services import llm_rate_limit

    fake_redis = FakeRedis()
    llm_rate_limit.set_redis_client_factory_for_tests(lambda _url: fake_redis)
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="external-ai-cache.db",
    )
    try:
        create_response = client.post(
            "/api/v1/integrations/ai/copy",
            headers=_authorized_headers(),
            json=_copy_payload(external_request_id="cache-hit-copy", count=1),
        )
        job_id = create_response.json()["data"]["job_id"]

        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await session.execute(delete(GenerationTask).where(GenerationTask.id == job_id))
            await session.commit()

        poll_response = client.get(
            f"/api/v1/integrations/ai/jobs/{job_id}",
            headers=_authorized_headers(),
        )
        body = poll_response.json()
    finally:
        app.dependency_overrides.clear()
        client.close()
        llm_rate_limit.set_redis_client_factory_for_tests(None)
        await engine.dispose()

    assert create_response.status_code == 202
    assert fake_redis.set_calls >= 1
    assert poll_response.status_code == 200
    assert body["code"] == 0
    assert body["data"]["job_id"] == job_id
    assert body["data"]["copywritings"]
