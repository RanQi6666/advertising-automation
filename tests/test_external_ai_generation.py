import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.services.generation_task_service as task_module
from backend.app.api.v1.endpoints.external_ai_generation import _read_reference_video_upload
from backend.app.core.config import get_settings
from backend.app.core.errors import AppError, ProviderError
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
    DirectorBeat,
    FrameAnalysis,
    FrameAnchoredDirectorPlan,
    FrameAnchoredStoryboard,
    FrameAnchoredStoryboardScene,
    FrameLanguageAnalysis,
    FrameTransitionBrief,
    FrameVisualFacts,
    ReferenceAdaptedConstraints,
    ReferenceBehaviorBeat,
    ReferenceBehaviorGraph,
    ReferenceCameraPattern,
    ReferenceConstraint,
    ReferenceSubjectPresence,
    ReferenceTransitionPattern,
    ReferenceVideoAnalysis,
    ReferenceVideoFrame,
    ReferenceVideoSegment,
    ReferenceVisualIdentityMapping,
    StoryboardSoundDesign,
    TimelineAdaptationBeat,
    TimelineAdaptationPlan,
    TopicCandidate,
    VideoStoryboardCandidate,
    VideoStoryboardScene,
)
from backend.app.schemas.external_ai_generation import (
    ExternalAIFrameAnchoredStoryboardCreate,
)
from backend.app.services import external_ai_generation_service as external_ai_service_module
from backend.app.services.external_ai_generation_service import ExternalAIGenerationService
from backend.app.services.storyboard_reference_video_service import PreparedReferenceVideo


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
    def __init__(self) -> None:
        self.calls: list[str] = []

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

    async def analyze_video_frame_pair(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        duration_seconds: int,
        aspect_ratio: str,
        reference_frames: list[ReferenceVideoFrame] | None = None,
        reference_video_duration_seconds: float | None = None,
        reference_video_sample_interval_seconds: float | None = None,
    ) -> FrameAnalysis:
        self.calls.append("analyze_video_frame_pair")
        assert first_frame_image_url.endswith("first.png")
        assert last_frame_image_url.endswith("last.png")
        assert duration_seconds == 12
        assert aspect_ratio == "9:16"
        if reference_frames:
            assert [frame.timestamp_seconds for frame in reference_frames] == [0.0, 2.0, 5.8]
            assert reference_video_duration_seconds == 5.8
            assert reference_video_sample_interval_seconds == 2.0
        return FrameAnalysis(
            first_frame=FrameVisualFacts(
                visible_subjects=["opening product"],
                visible_text=["START"],
                environment="studio",
                composition="centered",
                camera_perspective="eye level",
                visual_style="clean product video",
                color_and_lighting="bright",
                opening_state="product at rest",
            ),
            last_frame=FrameVisualFacts(
                visible_subjects=["ending product"],
                visible_text=["FINISH"],
                environment="studio",
                composition="centered",
                camera_perspective="eye level",
                visual_style="clean product video",
                color_and_lighting="bright",
                ending_state="product in final state",
            ),
            transition_brief=FrameTransitionBrief(
                shared_visual_facts=["same studio"],
                continuity_requirements=["preserve visible text"],
                visual_transition="move between supplied states",
                narrative_arc="opening to ending",
            ),
            language_analysis=FrameLanguageAnalysis(
                first_frame_visible_languages=["en"],
                last_frame_visible_languages=["en"],
                recommended_output_language="en",
                reason="Visible text is English.",
            ),
            reference_video_analysis=(
                ReferenceVideoAnalysis(
                    duration_seconds=5.8,
                    sample_interval_seconds=2.0,
                    segments=[
                        ReferenceVideoSegment(
                            start_second=0,
                            end_second=5.8,
                            subject_presence=ReferenceSubjectPresence(
                                state="target subject remains visible",
                                visibility="continuous",
                                screen_position="center",
                                movement="forward",
                                appearance=(
                                    "The subject enters from the right behind foreground light."
                                ),
                                action=(
                                    "The subject moves to center, turns toward the camera, "
                                    "and raises the target object."
                                ),
                                interaction="The gesture intensifies the surrounding particles.",
                            ),
                            camera=ReferenceCameraPattern(
                                movement="slow push-in",
                                intensity="medium",
                            ),
                            transition=ReferenceTransitionPattern(
                                type="continuous_motion",
                                description="Continuous motion between reference frames.",
                            ),
                            effects=["subtle particles"],
                            confidence="high",
                        )
                    ],
                    visual_identity_mappings=[
                        ReferenceVisualIdentityMapping(
                            reference_element="reward panel",
                            element_type="reward",
                            strategy="preserve_through_last_anchor",
                            instruction=(
                                "Keep the reward panel readable as a final overlay on the target "
                                "last-frame base layer."
                            ),
                        )
                    ],
                    behavior_graph=ReferenceBehaviorGraph(
                        entities=["subject", "target object", "reward panel"],
                        beats=[
                            ReferenceBehaviorBeat(
                                beat_id="approach",
                                reference_start_second=0,
                                reference_end_second=3,
                                description="The subject approaches the target object.",
                                visible_evidence=["subject", "target object"],
                                importance="core",
                                minimum_readable_duration_seconds=1,
                            ),
                            ReferenceBehaviorBeat(
                                beat_id="reward_overlay",
                                reference_start_second=4,
                                reference_end_second=5.8,
                                description="The reward panel appears and remains visible.",
                                visible_evidence=["reward panel"],
                                behavior_type="overlay",
                                importance="supporting",
                                minimum_readable_duration_seconds=1,
                                depends_on=["approach"],
                                must_remain_visible_until_final=True,
                            ),
                        ],
                    ),
                    adapted_constraints=ReferenceAdaptedConstraints(
                        subject_presence=ReferenceConstraint(
                            strength="preferred",
                            instruction=(
                                "Adapt the reference subject staging and action only when the "
                                "target frames confirm a compatible subject."
                            ),
                        ),
                        camera_pattern=ReferenceConstraint(
                            strength="preferred",
                            instruction="Prefer the reference camera rhythm where compatible.",
                        ),
                        transition_pattern=ReferenceConstraint(
                            strength="preferred",
                            instruction="Prefer the reference transition rhythm where compatible.",
                        ),
                        effects_pattern=ReferenceConstraint(
                            strength="preferred",
                            instruction="Adapt visible effects without copying reference content.",
                        ),
                    ),
                )
                if reference_frames
                else None
            ),
        )

    async def direct_frame_anchored_video_storyboard(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        frame_analysis: FrameAnalysis,
        duration_seconds: int,
        aspect_ratio: str,
    ) -> FrameAnchoredDirectorPlan:
        self.calls.append("direct_frame_anchored_video_storyboard")
        assert first_frame_image_url.endswith("first.png")
        assert last_frame_image_url.endswith("last.png")
        assert duration_seconds == 12
        assert aspect_ratio == "9:16"
        if frame_analysis.reference_video_analysis is not None:
            assert frame_analysis.timeline_adaptation_plan is not None
        return FrameAnchoredDirectorPlan(
            narrative_objective="Drive the observed action to a distinct visible result.",
            attention_path=["opening", "action", "impact", "ending"],
            tension_curve=["setup", "trigger", "escalation", "climax", "resolution"],
            climax_beats=[
                DirectorBeat(
                    beat_id="causal_peak",
                    stage="climax",
                    source_evidence=["The analyzed transition contains a visible causal action."],
                    start_ratio=0.4,
                    end_ratio=0.72,
                    attention_objective="Focus attention on the action result.",
                    camera_instruction="Use an in-shot push and reframing at impact.",
                    action_requirement="Show the action before its visible result.",
                    effect_requirement="Peak the observed effect at the visible impact.",
                    importance="core",
                )
            ],
            anchor_adaptation_plan=["Resolve to the supplied last-frame composition."],
            anti_flattening_constraints=[
                "Keep trigger, action, impact, and ending as distinct readable phases."
            ],
        )

    async def generate_frame_anchored_video_storyboard(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        frame_analysis: FrameAnalysis,
        duration_seconds: int,
        aspect_ratio: str,
    ) -> FrameAnchoredStoryboard:
        self.calls.append("generate_frame_anchored_video_storyboard")
        assert first_frame_image_url.endswith("first.png")
        assert last_frame_image_url.endswith("last.png")
        assert frame_analysis.first_frame.visible_text == ["START"]
        assert frame_analysis.director_plan is not None
        if frame_analysis.reference_video_analysis is not None:
            assert (
                frame_analysis.reference_video_analysis.adapted_constraints.subject_presence.strength
                == "preferred"
            )
            assert frame_analysis.timeline_adaptation_plan is not None
            assert frame_analysis.timeline_adaptation_plan.beats[-1].must_remain_visible_until_final
        return FrameAnchoredStoryboard(
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            scenes=[
                FrameAnchoredStoryboardScene(
                    scene_index=1,
                    start_second=0,
                    end_second=3,
                    frame_anchor="first_frame",
                    visual="Hold the supplied opening state.",
                    motion="Slow push in.",
                    transition_goal="Start at the supplied first frame.",
                    sound_effects=["soft room tone"],
                ),
                FrameAnchoredStoryboardScene(
                    scene_index=2,
                    start_second=3,
                    end_second=9,
                    frame_anchor="transition",
                    visual="Move through the observed visual change.",
                    motion="Follow the movement.",
                    transition_goal="Bridge the supplied frames.",
                    voiceover="Optional narration.",
                    sound_effects=["movement swish"],
                    cinematic_beat="causal_peak",
                    camera_instruction="Use an in-shot push and reframing at impact.",
                    tension_stage="climax",
                    action_result_requirement="Show the action before its visible result.",
                    effect_timing="Peak the observed effect at the visible impact.",
                    anti_flattening_requirement="Keep impact distinct from the final resolution.",
                ),
                FrameAnchoredStoryboardScene(
                    scene_index=3,
                    start_second=9,
                    end_second=12,
                    frame_anchor="last_frame",
                    visual="Arrive at the supplied ending state.",
                    motion="Settle into the final composition.",
                    transition_goal="End at the supplied last frame.",
                    sound_effects=["music resolve"],
                ),
            ],
            sound_design=StoryboardSoundDesign(music="gentle build", ambience="room tone"),
            rationale="Bridge the supplied frames without inventing unseen content.",
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


def test_external_storyboard_v2_accepts_practical_data_url_frames() -> None:
    image_data_url = "data:image/jpeg;base64," + ("A" * 5_000)

    request = ExternalAIFrameAnchoredStoryboardCreate.model_validate(
        _storyboard_v2_payload(
            first_frame_image_url=image_data_url,
            last_frame_image_url=image_data_url,
        )
    )

    assert request.first_frame_image_url == image_data_url
    assert request.last_frame_image_url == image_data_url


@pytest.mark.parametrize(
    "reference_video",
    [
        {"source_type": "url", "video_url": "https://cdn.example.test/reference.mp4"},
        {"source_type": "uploaded_asset", "upload_asset_id": "upload-reference-1"},
        {"source_type": "video_asset", "video_asset_id": "video-asset-1"},
    ],
)
def test_external_storyboard_v2_reference_video_schema_accepts_each_source(
    reference_video: dict[str, str],
) -> None:
    request = ExternalAIFrameAnchoredStoryboardCreate.model_validate(
        _storyboard_v2_payload(reference_video=reference_video)
    )

    assert request.reference_video is not None
    assert request.reference_video.source_type == reference_video["source_type"]


def test_external_storyboard_v2_reference_video_schema_remains_optional() -> None:
    request = ExternalAIFrameAnchoredStoryboardCreate.model_validate(_storyboard_v2_payload())

    assert request.reference_video is None


@pytest.mark.parametrize(
    "reference_video",
    [
        {"source_type": "url"},
        {
            "source_type": "url",
            "video_url": "https://cdn.example.test/reference.mp4",
            "video_asset_id": "wrong",
        },
        {"source_type": "uploaded_asset", "video_url": "https://cdn.example.test/reference.mp4"},
        {"source_type": "video_asset", "upload_asset_id": "wrong"},
    ],
)
def test_external_storyboard_v2_reference_video_schema_rejects_mismatched_source_fields(
    reference_video: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        ExternalAIFrameAnchoredStoryboardCreate.model_validate(
            _storyboard_v2_payload(reference_video=reference_video)
        )


@pytest.mark.asyncio
async def test_reference_video_upload_stops_reading_when_size_limit_is_exceeded() -> None:
    class ChunkedUpload:
        def __init__(self) -> None:
            self.read_sizes: list[int] = []
            self.chunks = [b"abc", b"def", b"should-not-be-read"]

        async def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return self.chunks.pop(0) if self.chunks else b""

    upload = ChunkedUpload()

    with pytest.raises(AppError, match="exceeds configured size limit"):
        await _read_reference_video_upload(upload, max_bytes=5, chunk_size=3)

    assert upload.read_sizes == [3, 3]


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
async def test_external_storyboard_v2_runs_two_steps_and_keeps_analysis_private(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="external-ai-storyboard-v2.db",
    )
    try:
        create_response = client.post(
            "/api/v1/integrations/ai/storyboard-v2",
            headers=_authorized_headers(),
            json=_storyboard_v2_payload(),
        )
        create_body = create_response.json()
        job_id = create_body["data"]["job_id"]
        poll_response = client.get(
            f"/api/v1/integrations/ai/jobs/{job_id}",
            headers=_authorized_headers(),
        )
        poll_body = poll_response.json()
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert create_response.status_code == 202
    assert poll_response.status_code == 200
    assert poll_body["code"] == 0
    assert poll_body["data"]["status"] == "succeeded"
    assert poll_body["data"]["request_id"] == "external-ai-storyboard-v2-1"
    assert poll_body["data"]["duration_seconds"] == 12
    assert poll_body["data"]["aspect_ratio"] == "9:16"
    assert "Scene 1" in poll_body["data"]["storyboard_text"]
    assert "Cinematic beat: causal_peak" in poll_body["data"]["storyboard_text"]
    assert "frame_analysis" not in poll_body["data"]
    assert "director_plan" not in poll_body["data"]
    assert "storyboard" not in poll_body["data"]
    assert fake_llm.calls == [
        "analyze_video_frame_pair",
        "direct_frame_anchored_video_storyboard",
        "generate_frame_anchored_video_storyboard",
    ]

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        task = await session.get(GenerationTask, job_id)
        assert task is not None
        assert task.queue_name == "text_queue"
        assert task.task_type == "external_video_storyboard_v2"
        assert task.campaign_id is None
        assert task.metadata_json is not None
        assert task.metadata_json["frame_analysis"]["first_frame"]["visible_text"] == ["START"]
        director_plan = task.metadata_json["frame_analysis"]["director_plan"]
        assert director_plan["climax_beats"][0]["beat_id"] == "causal_peak"
        first_scene = task.metadata_json["frame_anchored_storyboard"]["scenes"][0]
        assert first_scene["frame_anchor"] == "first_frame"

    assert await _count_rows(engine, Campaign) == 0
    assert await _count_rows(engine, WorkOrder) == 0
    assert await _count_rows(engine, ContentTopic) == 0
    assert await _count_rows(engine, CopyDraft) == 0
    assert await _count_rows(engine, CreativeAsset) == 0
    assert await _count_rows(engine, VideoAsset) == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_storyboard_v2_reference_video_runs_joint_analysis_then_storyboard(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()

    class FakeReferenceVideoService:
        async def prepare(self, session, reference_video, *, task_id: str):
            assert session is not None
            assert reference_video.source_type == "url"
            assert task_id
            return PreparedReferenceVideo(
                duration_seconds=5.8,
                sample_interval_seconds=2.0,
                frames=[
                    ReferenceVideoFrame(
                        timestamp_seconds=0,
                        image_url="data:image/jpeg;base64,AAA",
                    ),
                    ReferenceVideoFrame(
                        timestamp_seconds=2,
                        image_url="data:image/jpeg;base64,BBB",
                    ),
                    ReferenceVideoFrame(
                        timestamp_seconds=5.8,
                        image_url="data:image/jpeg;base64,CCC",
                    ),
                ],
                working_dir=tmp_path / "reference-analysis",
            )

        async def cleanup(self, prepared: PreparedReferenceVideo) -> None:
            assert prepared.duration_seconds == 5.8

    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    monkeypatch.setattr(
        external_ai_service_module,
        "StoryboardReferenceVideoService",
        FakeReferenceVideoService,
    )
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="external-ai-storyboard-v2-reference.db",
    )
    try:
        created = client.post(
            "/api/v1/integrations/ai/storyboard-v2",
            headers=_authorized_headers(),
            json=_storyboard_v2_payload(
                reference_video={
                    "source_type": "url",
                    "video_url": "https://cdn.example.test/reference.mp4",
                }
            ),
        )
        job_id = created.json()["data"]["job_id"]
        polled = client.get(
            f"/api/v1/integrations/ai/jobs/{job_id}",
            headers=_authorized_headers(),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    poll_data = polled.json()["data"]
    assert created.status_code == 202
    assert polled.status_code == 200
    assert poll_data["status"] == "succeeded"
    assert "Target timeline adaptation" in poll_data["storyboard_text"]
    assert (
        "required final overlay on the target last-frame base layer"
        in poll_data["storyboard_text"]
    )
    assert "reference_video_analysis" not in poll_data
    assert "reference_frames" not in poll_data
    assert fake_llm.calls == [
        "analyze_video_frame_pair",
        "direct_frame_anchored_video_storyboard",
        "generate_frame_anchored_video_storyboard",
    ]

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        task = await session.get(GenerationTask, job_id)
        assert task is not None
        assert task.metadata_json["frame_analysis"]["reference_video_analysis"][
            "adapted_constraints"
        ]["subject_presence"]["strength"] == "preferred"
        assert task.metadata_json["frame_analysis"]["timeline_adaptation_plan"]["beats"][-1][
            "must_remain_visible_until_final"
        ] is True
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_storyboard_v2_reference_failure_stops_before_storyboard_generation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()
    monkeypatch.setenv("GENERATION_TASK_AUTO_RETRY_ENABLED", "false")
    get_settings.cache_clear()

    class FailingReferenceVideoService:
        async def prepare(self, session, reference_video, *, task_id: str):
            del session, reference_video, task_id
            raise ProviderError("reference video analysis preparation failed")

    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    monkeypatch.setattr(
        external_ai_service_module,
        "StoryboardReferenceVideoService",
        FailingReferenceVideoService,
    )
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="external-ai-storyboard-v2-reference-failure.db",
    )
    try:
        created = client.post(
            "/api/v1/integrations/ai/storyboard-v2",
            headers=_authorized_headers(),
            json=_storyboard_v2_payload(
                reference_video={
                    "source_type": "url",
                    "video_url": "https://cdn.example.test/reference.mp4",
                }
            ),
        )
        job_id = created.json()["data"]["job_id"]
        polled = client.get(
            f"/api/v1/integrations/ai/jobs/{job_id}",
            headers=_authorized_headers(),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert polled.json()["data"]["status"] == "failed"
    assert "reference video analysis preparation failed" in polled.json()["data"]["error"]
    assert fake_llm.calls == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_storyboard_v2_reference_video_upload_returns_opaque_asset_id(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="external-ai-storyboard-v2-upload.db",
    )
    try:
        response = client.post(
            "/api/v1/integrations/ai/storyboard-v2/reference-video",
            headers=_authorized_headers(),
            files={"video": ("reference.mp4", b"video-bytes", "video/mp4")},
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    body = response.json()
    assert response.status_code == 201
    assert body["code"] == 0
    upload_asset_id = body["data"]["upload_asset_id"]
    assert upload_asset_id
    stored = list(
        (tmp_path / "storage" / "videos" / "storyboard_reference_uploads").glob(
            f"{upload_asset_id}.*"
        )
    )
    assert len(stored) == 1
    assert stored[0].read_bytes() == b"video-bytes"
    assert await _count_rows(engine, Campaign) == 0
    assert await _count_rows(engine, CreativeAsset) == 0
    assert await _count_rows(engine, VideoAsset) == 0
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("reference.avi", "video/x-msvideo", b"video-bytes"),
        ("reference.mp4", "video/mp4", b""),
    ],
)
async def test_external_storyboard_v2_reference_video_upload_rejects_invalid_input(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    filename: str,
    content_type: str,
    content: bytes,
) -> None:
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    get_settings.cache_clear()
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="external-ai-storyboard-v2-upload-invalid.db",
    )
    try:
        response = client.post(
            "/api/v1/integrations/ai/storyboard-v2/reference-video",
            headers=_authorized_headers(),
            files={"video": (filename, content, content_type)},
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 400
    assert response.json()["code"] == 4001
    assert not (tmp_path / "storage" / "videos" / "storyboard_reference_uploads").exists()
    assert await _count_rows(engine, Campaign) == 0
    assert await _count_rows(engine, CreativeAsset) == 0
    assert await _count_rows(engine, VideoAsset) == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_storyboard_v2_reference_video_upload_rejects_oversized_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCAL_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("VIDEO_DOWNLOAD_MAX_BYTES", "5")
    get_settings.cache_clear()
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="external-ai-storyboard-v2-upload-oversized.db",
    )
    try:
        response = client.post(
            "/api/v1/integrations/ai/storyboard-v2/reference-video",
            headers=_authorized_headers(),
            files={"video": ("reference.mp4", b"123456", "video/mp4")},
        )
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert response.status_code == 400
    assert response.json()["code"] == 4001
    assert "exceeds configured size limit" in response.json()["message"]
    assert not (tmp_path / "storage" / "videos" / "storyboard_reference_uploads").exists()
    assert await _count_rows(engine, Campaign) == 0
    assert await _count_rows(engine, CreativeAsset) == 0
    assert await _count_rows(engine, VideoAsset) == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_storyboard_v2_rejects_legacy_fields_with_4001(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="external-ai-storyboard-v2-validation.db",
    )
    try:
        response = client.post(
            "/api/v1/integrations/ai/storyboard-v2",
            headers=_authorized_headers(),
            json=_storyboard_v2_payload(brief="must not be accepted"),
        )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()

    assert response.status_code == 400
    assert response.json()["code"] == 4001


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


def test_frame_anchored_storyboard_does_not_export_machine_readable_final_text_lock() -> None:
    storyboard = FrameAnchoredStoryboard(
        duration_seconds=10,
        aspect_ratio="9:16",
        scenes=[
            FrameAnchoredStoryboardScene(
                scene_index=1,
                start_second=0,
                end_second=9.35,
                frame_anchor="first_frame",
                visual="Begin from the provided first frame.",
            ),
            FrameAnchoredStoryboardScene(
                scene_index=2,
                start_second=9.35,
                end_second=10,
                frame_anchor="last_frame",
                visual="Arrive at the provided final frame.",
            ),
        ],
    )
    plan = TimelineAdaptationPlan(
        reference_duration_seconds=10,
        target_duration_seconds=10,
        beats=[
            TimelineAdaptationBeat(
                beat_id="final_reward",
                description="The reward text holds through the final frame.",
                target_start_second=9.35,
                target_end_second=10,
                must_remain_visible_until_final=True,
                locked_text="x200,000",
                adaptation_instruction="Keep the overlay through the final frame.",
            )
        ],
    )

    text = external_ai_service_module._format_frame_anchored_storyboard_text(
        storyboard,
        timeline_adaptation_plan=plan,
    )

    assert "[FINAL_TEXT_OVERLAY_LOCKS]" not in text
    assert "[/FINAL_TEXT_OVERLAY_LOCKS]" not in text


def test_frame_anchored_storyboard_formats_freeform_overlay_instruction() -> None:
    storyboard = FrameAnchoredStoryboard(
        duration_seconds=10,
        aspect_ratio="9:16",
        scenes=[
            FrameAnchoredStoryboardScene(
                scene_index=1,
                start_second=0,
                end_second=5,
                frame_anchor="first_frame",
                visual="Begin from the supplied first frame.",
                overlay_instruction="Introduce the observed interface after activation.",
            ),
            FrameAnchoredStoryboardScene(
                scene_index=2,
                start_second=5,
                end_second=10,
                frame_anchor="last_frame",
                visual="Resolve on the supplied last frame.",
                overlay_instruction="Keep the selected overlay readable through the ending.",
            ),
        ],
    )

    text = external_ai_service_module._format_frame_anchored_storyboard_text(storyboard)

    assert (
        "Overlay lifecycle: Keep the selected overlay readable through the ending."
        in text
    )
