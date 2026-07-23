import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import backend.app.services.generation_task_service as task_module
from backend.app.api.v1.endpoints.external_ai_generation import _read_reference_video_upload
from backend.app.core.config import get_settings
from backend.app.core.errors import AppError, ProviderError
from backend.app.db.base import Base, utcnow
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
    DirectorActionCorrection,
    DirectorActionCoverageReview,
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
    TopicCandidate,
    VideoStoryboardCandidate,
    VideoStoryboardScene,
)
from backend.app.schemas.external_ai_generation import (
    ExternalAIFrameAnchoredStoryboardCreate,
)
from backend.app.services import external_ai_generation_service as external_ai_service_module
from backend.app.services.external_ai_generation_service import (
    ExternalAIGenerationService,
    _format_frame_anchored_storyboard_text,
    _format_optional_intensity,
    _normalize_private_storyboard_namespace,
    _replace_private_id_aliases,
    _replace_private_ids_in_text,
)
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
        self.call_details: list[dict[str, object]] = []

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
            action_arc_windows=[
                {
                    "window_id": "preparation",
                    "phase": "preparation",
                    "start_ratio": 0.0,
                    "end_ratio": 0.2,
                    "objective": "Prepare the subject action from the opening anchor.",
                    "subject_motion_intensity": 0.3,
                    "camera_intensity": 0.2,
                    "effect_intensity": 0.1,
                },
                {
                    "window_id": "action",
                    "phase": "action",
                    "start_ratio": 0.2,
                    "end_ratio": 0.55,
                    "objective": "Execute the evidence-backed subject action.",
                    "subject_motion_intensity": 0.9,
                    "camera_intensity": 0.6,
                    "effect_intensity": 0.4,
                    "depends_on": ["preparation"],
                },
                {
                    "window_id": "payoff",
                    "phase": "payoff",
                    "start_ratio": 0.55,
                    "end_ratio": 0.72,
                    "objective": "Reveal the visible result of the action.",
                    "subject_motion_intensity": 0.5,
                    "camera_intensity": 0.5,
                    "effect_intensity": 0.7,
                    "depends_on": ["action"],
                },
                {
                    "window_id": "return",
                    "phase": "return",
                    "start_ratio": 0.72,
                    "end_ratio": 0.88,
                    "objective": "Return continuously to the ending anchor.",
                    "subject_motion_intensity": 0.4,
                    "camera_intensity": 0.3,
                    "effect_intensity": 0.2,
                    "depends_on": ["payoff"],
                },
                {
                    "window_id": "final_lock",
                    "phase": "final_lock",
                    "start_ratio": 0.88,
                    "end_ratio": 1.0,
                    "objective": "Hold and lock the supplied final anchor.",
                    "subject_motion_intensity": 0.05,
                    "camera_intensity": 0.05,
                    "effect_intensity": 0.0,
                    "depends_on": ["return"],
                },
            ],
            signature_moment_plan=[
                {
                    "moment_id": "signature_action",
                    "moment_type": "combined",
                    "source_evidence": [
                        "The analyzed transition contains a visible causal action."
                    ],
                    "source_behavior_beat_ids": ["approach"],
                    "transfer_role": "primary_action",
                    "strategy": "adapt",
                    "target_adaptation": "Adapt the approach to the supplied target frames.",
                    "adapted_action": "Execute the target-compatible subject approach.",
                    "temporary_divergence": "Depart temporarily from the opening composition.",
                    "camera_support": "Use an in-shot push and reframe around execution.",
                    "effect_support": "Peak effects only after subject motion is readable.",
                    "visible_payoff": "Show the visible result of the completed approach.",
                    "return_strategy": "Return continuously to the supplied last frame.",
                    "assigned_beat_id": "causal_peak",
                }
            ],
            final_anchor_return="Return continuously and settle into the supplied last frame.",
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
        director_corrections: list[DirectorActionCorrection] | None = None,
    ) -> FrameAnchoredStoryboard:
        self.calls.append("generate_frame_anchored_video_storyboard")
        self.call_details.append(
            {
                "director_corrections": director_corrections,
            }
        )
        assert first_frame_image_url.endswith("first.png")
        assert last_frame_image_url.endswith("last.png")
        assert frame_analysis.first_frame.visible_text == ["START"]
        assert frame_analysis.director_plan is not None
        director_plan = frame_analysis.director_plan
        signature_moment = director_plan.signature_moment_plan[0]
        signature_moment_ids = list(
            dict.fromkeys([signature_moment.moment_id, "custom_signature_id"])
        )
        source_behavior_beat_ids = list(
            dict.fromkeys(
                [
                    *signature_moment.source_behavior_beat_ids,
                    "core_behavior",
                    "core_state_change",
                    "custom_behavior_beat",
                ]
            )
        )
        assigned_beat_id = signature_moment.assigned_beat_id
        assert assigned_beat_id is not None
        if frame_analysis.reference_video_analysis is not None:
            assert (
                frame_analysis.reference_video_analysis.adapted_constraints.subject_presence.strength
                == "preferred"
            )
            assert frame_analysis.timeline_adaptation_plan is not None
            assert frame_analysis.timeline_adaptation_plan.beats[-1].must_remain_visible_until_final

        def phase_evidence(phase: str) -> list[dict[str, object]]:
            return [
                {
                    "phase": phase,
                    "signature_moment_ids": [signature_moment.moment_id],
                    "source_behavior_beat_ids": list(signature_moment.source_behavior_beat_ids),
                }
            ]

        return FrameAnchoredStoryboard(
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            scenes=[
                FrameAnchoredStoryboardScene(
                    scene_index=1,
                    start_second=0,
                    end_second=2.4,
                    frame_anchor="first_frame",
                    visual="Prepare from the supplied opening state.",
                    motion="Prepare the subject for the continuous causal action.",
                    transition_goal="Preparation begins at the supplied first frame.",
                    sound_effects=["soft room tone"],
                    signature_moment_ids=signature_moment_ids,
                    source_behavior_beat_ids=source_behavior_beat_ids,
                    phase_evidence=phase_evidence("preparation"),
                ),
                FrameAnchoredStoryboardScene(
                    scene_index=2,
                    start_second=2.4,
                    end_second=6.6,
                    frame_anchor="transition",
                    visual="Execute the observed target-compatible visual change.",
                    motion="The subject performs the linked causal action.",
                    transition_goal="Temporarily depart while executing the linked action.",
                    voiceover="Optional narration.",
                    sound_effects=["movement swish"],
                    cinematic_beat=assigned_beat_id,
                    cinematic_beats=[assigned_beat_id, "custom_director_beat"],
                    signature_moment_ids=signature_moment_ids,
                    source_behavior_beat_ids=source_behavior_beat_ids,
                    execution_evidence=[
                        {
                            "claim_id": "execution_claim",
                            "executor_kind": "target_subject",
                            "assertion": "affirmed",
                            "action_or_state_change": "linked causal action completes visibly",
                            "signature_moment_ids": signature_moment_ids,
                            "source_behavior_beat_ids": source_behavior_beat_ids,
                        }
                    ],
                    camera_instruction="Use an in-shot push and reframing at impact.",
                    tension_stage="climax",
                    effect_timing="Keep effects subordinate until the action reads.",
                    subject_motion_intensity=0.9,
                    camera_intensity=0.6,
                    effect_intensity=0.4,
                    anti_flattening_requirement="Keep action distinct from its result.",
                    phase_evidence=phase_evidence("action"),
                ),
                FrameAnchoredStoryboardScene(
                    scene_index=3,
                    start_second=6.6,
                    end_second=8.64,
                    frame_anchor="transition",
                    visual="Show the visible result after the linked action.",
                    motion="The visible consequence reads before the return.",
                    transition_goal="Hold the payoff clearly without a cut.",
                    cinematic_beat=assigned_beat_id,
                    cinematic_beats=[assigned_beat_id, "custom_director_beat"],
                    signature_moment_ids=signature_moment_ids,
                    source_behavior_beat_ids=source_behavior_beat_ids,
                    camera_instruction="Reframe gently around the visible result.",
                    action_result_requirement=(
                        "Show "
                        + ", ".join(
                            [
                                *source_behavior_beat_ids,
                                *signature_moment_ids,
                                assigned_beat_id,
                                "custom_director_beat",
                            ]
                        )
                        + " only through their visible results."
                    ),
                    effect_timing="Peak the observed effect at the visible impact.",
                    phase_evidence=phase_evidence("payoff"),
                ),
                FrameAnchoredStoryboardScene(
                    scene_index=4,
                    start_second=8.64,
                    end_second=10.56,
                    frame_anchor="transition",
                    visual="Return continuously toward the ending composition.",
                    motion="Settle the subject toward the supplied ending state.",
                    transition_goal="Restore the final anchor in the same shot.",
                    signature_moment_ids=signature_moment_ids,
                    source_behavior_beat_ids=source_behavior_beat_ids,
                    anchor_return_instruction=("Return continuously to the supplied last frame."),
                    phase_evidence=phase_evidence("return"),
                ),
                FrameAnchoredStoryboardScene(
                    scene_index=5,
                    start_second=10.56,
                    end_second=12,
                    frame_anchor="last_frame",
                    visual="Arrive, hold, and lock the supplied ending state.",
                    motion="Settle and stabilize the subject in the final composition.",
                    transition_goal="End and hold on the supplied last frame.",
                    sound_effects=["music resolve"],
                    signature_moment_ids=signature_moment_ids,
                    source_behavior_beat_ids=source_behavior_beat_ids,
                    phase_evidence=phase_evidence("final_hold"),
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


def test_frame_anchored_storyboard_text_hides_multiple_director_beats() -> None:
    storyboard = FrameAnchoredStoryboard(
        duration_seconds=10,
        aspect_ratio="9:16",
        scenes=[
            FrameAnchoredStoryboardScene(
                scene_index=1,
                start_second=0,
                end_second=7,
                frame_anchor="first_frame",
                visual="Carry the continuous causal action.",
                cinematic_beat="legacy_label",
                cinematic_beats=["cause", "action", "impact"],
            ),
            FrameAnchoredStoryboardScene(
                scene_index=2,
                start_second=7,
                end_second=10,
                frame_anchor="last_frame",
                visual="Resolve on the supplied ending.",
                cinematic_beats=["visible_result"],
            ),
        ],
    )

    storyboard_text = _format_frame_anchored_storyboard_text(storyboard)

    assert "Cinematic beat:" not in storyboard_text
    assert "Cinematic beats:" not in storyboard_text
    assert "visible_result" not in storyboard_text
    assert "legacy_label" not in storyboard_text
    assert "Visual: Carry the continuous causal action." in storyboard_text
    assert "Visual: Resolve on the supplied ending." in storyboard_text


def test_external_storyboard_v2_rejects_legacy_and_missing_frame_fields() -> None:
    request = ExternalAIFrameAnchoredStoryboardCreate.model_validate(_storyboard_v2_payload())

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
    data = poll_body["data"]
    assert data["status"] == "succeeded"
    assert data["request_id"] == "external-ai-storyboard-v2-1"
    assert data["storyboard_text"]
    assert data["duration_seconds"] == 12
    assert data["aspect_ratio"] == "9:16"
    assert "Scene 1" in data["storyboard_text"]
    assert "Cinematic beat:" not in data["storyboard_text"]
    assert "Cinematic beats:" not in data["storyboard_text"]
    assert (
        "Return to final anchor: Return continuously to the supplied last frame."
        in data["storyboard_text"]
    )
    for private_id in (
        "core_behavior",
        "core_state_change",
        "custom_behavior_beat",
        "signature_action",
        "custom_signature_id",
        "causal_peak",
        "custom_director_beat",
    ):
        assert private_id not in data["storyboard_text"]
    for private_field in (
        "frame_analysis",
        "director_plan",
        "director_action_coverage_review",
        "frame_anchored_storyboard_candidate",
        "frame_anchored_storyboard",
        "signature_moment_ids",
        "source_behavior_beat_ids",
        "execution_evidence",
        "claim_id",
        "storyboard",
    ):
        assert private_field not in data
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
        assert task.metadata_json["director_action_coverage_review"]["status"] == "pass"
        director_plan = task.metadata_json["frame_analysis"]["director_plan"]
        assert director_plan["climax_beats"][0]["beat_id"] == "__sbv2_beat_001__"
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
    assert "Target timeline adaptation" not in poll_data["storyboard_text"]
    assert (
        "required final overlay on the target last-frame base layer"
        not in poll_data["storyboard_text"]
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
        assert (
            task.metadata_json["frame_analysis"]["reference_video_analysis"]["adapted_constraints"][
                "subject_presence"
            ]["strength"]
            == "preferred"
        )
        assert (
            task.metadata_json["frame_analysis"]["timeline_adaptation_plan"]["beats"][-1][
                "must_remain_visible_until_final"
            ]
            is True
        )
        assert task.metadata_json["director_action_coverage_review"]["status"] == "pass"
    await engine.dispose()


@pytest.mark.asyncio
async def test_external_storyboard_v2_retry_reuses_cached_analysis_and_director_plan(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingOnceStoryboardLLM(FakeExternalAILLM):
        def __init__(self) -> None:
            super().__init__()
            self.storyboard_attempts = 0

        async def generate_frame_anchored_video_storyboard(self, *args, **kwargs):
            self.storyboard_attempts += 1
            if self.storyboard_attempts == 1:
                self.calls.append("generate_frame_anchored_video_storyboard")
                raise ProviderError("temporary final storyboard failure")
            return await super().generate_frame_anchored_video_storyboard(*args, **kwargs)

    fake_llm = FailingOnceStoryboardLLM()

    class CountingReferenceVideoService:
        prepare_calls = 0
        cleanup_calls = 0

        async def prepare(self, session, reference_video, *, task_id: str):
            del session, reference_video, task_id
            type(self).prepare_calls += 1
            return PreparedReferenceVideo(
                duration_seconds=5.8,
                sample_interval_seconds=2.0,
                frames=[
                    ReferenceVideoFrame(
                        frame_index=0,
                        timestamp_seconds=0.0,
                        image_url="data:image/jpeg;base64,AAA",
                    ),
                    ReferenceVideoFrame(
                        frame_index=1,
                        timestamp_seconds=2.0,
                        image_url="data:image/jpeg;base64,BBB",
                    ),
                    ReferenceVideoFrame(
                        frame_index=2,
                        timestamp_seconds=5.8,
                        image_url="data:image/jpeg;base64,CCC",
                    ),
                ],
                working_dir=tmp_path / "cached-reference-analysis",
            )

        async def cleanup(self, prepared: PreparedReferenceVideo) -> None:
            del prepared
            type(self).cleanup_calls += 1

    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    monkeypatch.setattr(
        external_ai_service_module,
        "StoryboardReferenceVideoService",
        CountingReferenceVideoService,
    )
    engine, session_factory = await _session_factory(
        tmp_path,
        "external-ai-storyboard-v2-checkpoint.db",
    )
    service = ExternalAIGenerationService()
    async with session_factory() as session:
        task = GenerationTask(
            queue_name="text_queue",
            task_type="external_video_storyboard_v2",
            business_type="external_ai",
            business_id="checkpoint-storyboard-v2",
            payload_json=_storyboard_v2_payload(
                reference_video={
                    "source_type": "url",
                    "video_url": "https://cdn.example.test/reference.mp4",
                }
            ),
            queued_at=utcnow(),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        with pytest.raises(ProviderError, match="temporary final storyboard failure"):
            await service.execute_frame_anchored_video_storyboard(session, task)

        await session.refresh(task)
        assert task.metadata_json["frame_analysis"]["director_plan"] is not None

        result = await service.execute_frame_anchored_video_storyboard(session, task)

        assert "storyboard_text" in result
        assert fake_llm.calls == [
            "analyze_video_frame_pair",
            "direct_frame_anchored_video_storyboard",
            "generate_frame_anchored_video_storyboard",
            "generate_frame_anchored_video_storyboard",
        ]
        assert CountingReferenceVideoService.prepare_calls == 1
        assert CountingReferenceVideoService.cleanup_calls == 1
        assert fake_llm.call_details[-1]["director_corrections"] == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_external_storyboard_v2_retry_reuses_cached_analysis_after_director_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingOnceDirectorLLM(FakeExternalAILLM):
        def __init__(self) -> None:
            super().__init__()
            self.director_attempts = 0

        async def direct_frame_anchored_video_storyboard(self, *args, **kwargs):
            self.director_attempts += 1
            if self.director_attempts == 1:
                self.calls.append("direct_frame_anchored_video_storyboard")
                raise ProviderError("temporary director failure")
            return await super().direct_frame_anchored_video_storyboard(*args, **kwargs)

    fake_llm = FailingOnceDirectorLLM()
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    engine, session_factory = await _session_factory(
        tmp_path,
        "external-ai-storyboard-v2-analysis-checkpoint.db",
    )
    service = ExternalAIGenerationService()
    async with session_factory() as session:
        task = GenerationTask(
            queue_name="text_queue",
            task_type="external_video_storyboard_v2",
            business_type="external_ai",
            business_id="analysis-checkpoint-storyboard-v2",
            payload_json=_storyboard_v2_payload(),
            queued_at=utcnow(),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        with pytest.raises(ProviderError, match="temporary director failure"):
            await service.execute_frame_anchored_video_storyboard(session, task)

        await session.refresh(task)
        assert task.metadata_json["frame_analysis"]["director_plan"] is None

        result = await service.execute_frame_anchored_video_storyboard(session, task)

        assert "storyboard_text" in result
        assert fake_llm.calls == [
            "analyze_video_frame_pair",
            "direct_frame_anchored_video_storyboard",
            "direct_frame_anchored_video_storyboard",
            "generate_frame_anchored_video_storyboard",
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_storyboard_v2_stores_review_and_forwards_corrections(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    monkeypatch.setattr(
        external_ai_service_module,
        "review_director_action_coverage",
        lambda frame_analysis, director_plan: DirectorActionCoverageReview(
            status="corrective",
            required_core_behavior_beat_ids=["core_behavior"],
            uncovered_core_behavior_beat_ids=["core_behavior"],
            correction_requirements=[
                "Provide executable subject/state action and visible payoff for the linked moment."
            ],
            structured_corrections=[
                DirectorActionCorrection(
                    correction_type="execution",
                    signature_moment_ids=["signature_action"],
                    source_behavior_beat_ids=["core_behavior"],
                    instruction=(
                        "Provide executable subject/state action and visible payoff for the "
                        "linked moment."
                    ),
                )
            ],
        ),
    )
    monkeypatch.setattr(
        external_ai_service_module,
        "validate_final_storyboard_action_coverage",
        lambda storyboard, frame_analysis, review: None,
    )
    engine, session_factory = await _session_factory(tmp_path, "storyboard-v2-review.db")
    service = ExternalAIGenerationService()
    async with session_factory() as session:
        task = GenerationTask(
            queue_name="text_queue",
            task_type="external_video_storyboard_v2",
            business_type="external_ai",
            business_id="storyboard-v2-review",
            payload_json=_storyboard_v2_payload(),
            queued_at=utcnow(),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        result = await service.execute_frame_anchored_video_storyboard(session, task)
        await session.refresh(task)
        assert result["storyboard_text"]
        assert task.metadata_json["director_action_coverage_review"]["status"] == "corrective"
        forwarded = fake_llm.call_details[-1]["director_corrections"]
        assert len(forwarded) == 1
        assert forwarded[0].signature_moment_ids == ["signature_action"]
        assert forwarded[0].source_behavior_beat_ids == ["core_behavior"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_storyboard_v2_unrecoverable_linkage_stops_before_call3(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    monkeypatch.setattr(
        external_ai_service_module,
        "review_director_action_coverage",
        lambda frame_analysis, director_plan: DirectorActionCoverageReview.model_construct(
            status="unrecoverable",
            required_core_behavior_beat_ids=["core_behavior"],
            uncovered_core_behavior_beat_ids=["core_behavior"],
            correction_requirements=[],
            structured_corrections=[],
            unrecoverable_reasons=[
                "Core behavior beat core_behavior has no signature/source linkage."
            ],
        ),
    )
    engine, session_factory = await _session_factory(
        tmp_path, "storyboard-v2-unrecoverable-review.db"
    )
    service = ExternalAIGenerationService()
    async with session_factory() as session:
        task = GenerationTask(
            queue_name="text_queue",
            task_type="external_video_storyboard_v2",
            business_type="external_ai",
            business_id="storyboard-v2-unrecoverable-review",
            payload_json=_storyboard_v2_payload(),
            queued_at=utcnow(),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        with pytest.raises(ProviderError) as excinfo:
            await service.execute_frame_anchored_video_storyboard(session, task)

        message = str(excinfo.value)
        assert "required signature/source linkage is missing" in message
        assert "invalid omission contract" not in message
        assert "core_behavior" not in message
        assert fake_llm.calls == [
            "analyze_video_frame_pair",
            "direct_frame_anchored_video_storyboard",
        ]

    await engine.dispose()


@pytest.mark.asyncio
async def test_storyboard_v2_uncached_success_uses_exactly_three_llm_calls(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    engine, session_factory = await _session_factory(tmp_path, "storyboard-v2-three-calls.db")
    service = ExternalAIGenerationService()
    async with session_factory() as session:
        task = GenerationTask(
            queue_name="text_queue",
            task_type="external_video_storyboard_v2",
            business_type="external_ai",
            business_id="storyboard-v2-three-calls",
            payload_json=_storyboard_v2_payload(),
            queued_at=utcnow(),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        await service.execute_frame_anchored_video_storyboard(session, task)
        assert fake_llm.calls == [
            "analyze_video_frame_pair",
            "direct_frame_anchored_video_storyboard",
            "generate_frame_anchored_video_storyboard",
        ]
    await engine.dispose()


def test_format_optional_intensity_compacts_none_zero_and_fractional_values() -> None:
    assert _format_optional_intensity(None) == "-"
    assert _format_optional_intensity(0.0) == "0"
    assert _format_optional_intensity(0.25) == "0.25"
    assert _format_optional_intensity(0.05) == "0.05"


def test_frame_anchored_formatter_hides_private_ids() -> None:
    storyboard = FrameAnchoredStoryboard.model_validate(
        {
            "duration_seconds": 4,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 1,
                    "frame_anchor": "first_frame",
                    "visual": "Hold the exact supplied opening anchor.",
                },
                {
                    "scene_index": 2,
                    "start_second": 1,
                    "end_second": 3,
                    "frame_anchor": "transition",
                    "visual": "Execute a target-compatible causal action.",
                    "motion": "Complete readable subject/state motion.",
                    "camera_instruction": (
                        "Track the action payoff without losing endpoint compatibility."
                    ),
                    "transition_goal": "Bridge opening cause to ending payoff.",
                    "action_result_requirement": "Show the action before the visible result.",
                    "effect_timing": "Peak after the action reads.",
                    "signature_moment_ids": ["signature_action"],
                    "source_behavior_beat_ids": ["core_behavior"],
                    "cinematic_beats": ["core_peak"],
                    "subject_motion_intensity": 0.9,
                    "camera_intensity": 0.6,
                    "effect_intensity": 0.7,
                    "anchor_return_instruction": "Return continuously to the final anchor.",
                },
                {
                    "scene_index": 3,
                    "start_second": 3,
                    "end_second": 4,
                    "frame_anchor": "last_frame",
                    "visual": "Lock the exact supplied last frame.",
                },
            ],
        }
    )
    text = _format_frame_anchored_storyboard_text(storyboard)
    assert "signature_action" not in text
    assert "core_behavior" not in text
    assert "core_peak" not in text
    assert "Cinematic beat:" not in text
    assert "Cinematic beats:" not in text
    assert "Subject motion intensity:" in text
    assert "Camera intensity:" in text
    assert "Effect intensity:" in text
    assert "Return to final anchor:" in text


@pytest.mark.asyncio
async def test_external_storyboard_v2_saves_candidate_before_director_coverage_validation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()

    def reject_coverage(storyboard, plan) -> None:
        del storyboard, plan
        raise ValueError("missing dynamic core beat")

    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    monkeypatch.setattr(
        external_ai_service_module,
        "validate_director_coverage",
        reject_coverage,
    )
    engine, session_factory = await _session_factory(
        tmp_path,
        "external-ai-storyboard-v2-candidate.db",
    )
    service = ExternalAIGenerationService()
    async with session_factory() as session:
        task = GenerationTask(
            queue_name="text_queue",
            task_type="external_video_storyboard_v2",
            business_type="external_ai",
            business_id="candidate-storyboard-v2",
            payload_json=_storyboard_v2_payload(),
            queued_at=utcnow(),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        with pytest.raises(
            ProviderError,
            match="does not execute the required director action and final-anchor return",
        ):
            await service.execute_frame_anchored_video_storyboard(session, task)

        await session.refresh(task)
        candidate = task.metadata_json["frame_anchored_storyboard_candidate"]
        assert candidate["scenes"][1]["cinematic_beat"] == "__sbv2_beat_001__"
        assert "frame_anchored_storyboard" not in task.metadata_json

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


def test_frame_anchored_storyboard_uses_final_storyboard_as_only_public_execution_plan() -> None:
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
                visual=(
                    "Arrive at the provided final frame and remove the inherited reward "
                    "overlay before the ending anchor."
                ),
                notes="The final-frame base layer is the sole visible ending state.",
            ),
        ],
    )
    text = external_ai_service_module._format_frame_anchored_storyboard_text(storyboard)

    assert "[FINAL_TEXT_OVERLAY_LOCKS]" not in text
    assert "[/FINAL_TEXT_OVERLAY_LOCKS]" not in text
    assert "remove the inherited reward overlay" in text
    assert "The final-frame base layer is the sole visible ending state." in text
    assert "Target timeline adaptation" not in text
    assert "required final overlay" not in text
    assert "x200,000" not in text
    assert "Keep the overlay through the final frame." not in text


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

    assert "Overlay lifecycle: Keep the selected overlay readable through the ending." in text


def test_frame_anchored_formatter_scrubs_private_ids_from_renderable_text() -> None:
    private_ids = (
        "core_behavior",
        "core_state_change",
        "custom_behavior_beat",
        "custom_signature_id",
        "custom_director_beat",
    )
    leaked = " ".join(private_ids)
    storyboard = FrameAnchoredStoryboard(
        duration_seconds=4,
        aspect_ratio="9:16",
        scenes=[
            FrameAnchoredStoryboardScene(
                scene_index=1,
                start_second=0,
                end_second=1,
                frame_anchor="first_frame",
                visual="Opening anchor.",
            ),
            FrameAnchoredStoryboardScene(
                scene_index=2,
                start_second=1,
                end_second=4,
                frame_anchor="last_frame",
                visual=leaked,
                motion=leaked,
                transition_goal=leaked,
                camera_instruction=leaked,
                action_result_requirement=leaked,
                effect_timing=leaked,
                anchor_return_instruction=leaked,
                notes=leaked,
                sound_effects=[leaked],
                cinematic_beat="custom_director_beat",
                cinematic_beats=["custom_director_beat"],
                signature_moment_ids=["custom_signature_id"],
                source_behavior_beat_ids=[
                    "core_behavior",
                    "core_state_change",
                    "custom_behavior_beat",
                ],
                execution_evidence=[
                    {
                        "claim_id": "__sbv2_claim_001__",
                        "executor_kind": "target_subject",
                        "assertion": "affirmed",
                        "action_or_state_change": "linked state changes visibly",
                        "signature_moment_ids": ["custom_signature_id"],
                        "source_behavior_beat_ids": ["core_behavior"],
                    }
                ],
            ),
        ],
        sound_design=StoryboardSoundDesign(music=leaked, ambience=leaked),
        rationale=leaked,
    )

    text = _format_frame_anchored_storyboard_text(storyboard)

    for private_id in private_ids:
        assert private_id not in text


@pytest.mark.asyncio
async def test_storyboard_v2_invalid_omit_stops_after_call2(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()
    original_analyze = fake_llm.analyze_video_frame_pair
    original_direct = fake_llm.direct_frame_anchored_video_storyboard

    async def analyze_with_required_core_behavior(
        first_frame_image_url,
        last_frame_image_url,
        duration_seconds,
        aspect_ratio,
        **kwargs,
    ):
        del kwargs
        return await original_analyze(
            first_frame_image_url,
            last_frame_image_url,
            duration_seconds,
            aspect_ratio,
            reference_frames=[
                ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA"),
                ReferenceVideoFrame(timestamp_seconds=2, image_url="data:image/jpeg;base64,BBB"),
                ReferenceVideoFrame(timestamp_seconds=5.8, image_url="data:image/jpeg;base64,CCC"),
            ],
            reference_video_duration_seconds=5.8,
            reference_video_sample_interval_seconds=2.0,
        )

    async def invalid_omit_director(*args, **kwargs):
        plan = await original_direct(*args, **kwargs)
        moment = plan.signature_moment_plan[0].model_copy(
            update={
                "strategy": "omit",
                "adapted_action": "",
                "temporary_divergence": "",
                "camera_support": "",
                "effect_support": "",
                "visible_payoff": "",
                "return_strategy": "",
                "assigned_beat_id": None,
                "omission_reason": "The action cannot match the final pose.",
                "equivalent_replacement_failure": ("No equivalent preserves the ending framing."),
                "literal_infeasibility_category": "endpoint_constraint_only",
                "literal_infeasibility_evidence": "Only the final pose differs.",
                "equivalent_infeasibility_category": "endpoint_constraint_only",
                "equivalent_infeasibility_evidence": "Only the ending framing differs.",
            }
        )
        return plan.model_copy(update={"signature_moment_plan": [moment]})

    fake_llm.analyze_video_frame_pair = analyze_with_required_core_behavior
    fake_llm.direct_frame_anchored_video_storyboard = invalid_omit_director
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    engine, session_factory = await _session_factory(tmp_path, "storyboard-v2-invalid-omit.db")
    service = ExternalAIGenerationService()
    async with session_factory() as session:
        task = GenerationTask(
            queue_name="text_queue",
            task_type="external_video_storyboard_v2",
            business_type="external_ai",
            business_id="storyboard-v2-invalid-omit",
            payload_json=_storyboard_v2_payload(),
            queued_at=utcnow(),
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

        with pytest.raises(ProviderError) as excinfo:
            await service.execute_frame_anchored_video_storyboard(session, task)

        message = str(excinfo.value)
        assert "invalid omission contract" in message
        assert "signature/source linkage" not in message
        assert "signature_action" not in message
        assert "core_behavior" not in message
        assert fake_llm.calls == [
            "analyze_video_frame_pair",
            "direct_frame_anchored_video_storyboard",
        ]

    await engine.dispose()


def test_formatter_scrubs_only_safe_private_ids_and_preserves_natural_language() -> None:
    private_ids = (
        "__sbv2_behavior_001__",
        "__sbv2_beat_001__",
        "__sbv2_window_001__",
        "__sbv2_moment_001__",
        "__sbv2_claim_001__",
    )
    natural_sentence = "The camera follows the action while the subject moves."
    storyboard = FrameAnchoredStoryboard(
        duration_seconds=4,
        aspect_ratio="9:16",
        scenes=[
            FrameAnchoredStoryboardScene(
                scene_index=1,
                start_second=0,
                end_second=1,
                frame_anchor="first_frame",
                visual="Opening anchor.",
            ),
            FrameAnchoredStoryboardScene(
                scene_index=2,
                start_second=1,
                end_second=4,
                frame_anchor="last_frame",
                visual=f"{' '.join(private_ids)}; {natural_sentence}",
                execution_evidence=[
                    {
                        "claim_id": private_ids[4],
                        "executor_kind": "target_subject",
                        "assertion": "affirmed",
                        "action_or_state_change": "linked action changes state visibly",
                        "signature_moment_ids": [private_ids[3]],
                        "source_behavior_beat_ids": [private_ids[0]],
                    }
                ],
            ),
        ],
    )

    rendered = _format_frame_anchored_storyboard_text(
        storyboard,
        private_sources=(
            {"behavior_graph": {"beats": [{"beat_id": private_ids[0]}]}},
            {"climax_beats": [{"beat_id": private_ids[1]}]},
            {"action_arc_windows": [{"window_id": private_ids[2]}]},
            {"signature_moment_plan": [{"moment_id": private_ids[3]}]},
            {"ordinary_ids": ["camera", "action", "subject"]},
        ),
    )

    for private_id in private_ids:
        assert private_id not in rendered
    assert natural_sentence in rendered


@pytest.mark.asyncio
async def test_private_namespace_normalization_revalidates_nested_models() -> None:
    fake_llm = FakeExternalAILLM()
    frame_analysis = await fake_llm.analyze_video_frame_pair(
        "https://cdn.example.test/first.png",
        "https://cdn.example.test/last.png",
        12,
        "9:16",
    )
    plan = await fake_llm.direct_frame_anchored_video_storyboard(
        "https://cdn.example.test/first.png",
        "https://cdn.example.test/last.png",
        frame_analysis,
        12,
        "9:16",
    )
    duplicate_moment_plan = plan.model_copy(
        update={
            "signature_moment_plan": [
                plan.signature_moment_plan[0],
                plan.signature_moment_plan[0].model_copy(),
            ]
        }
    )
    invalid_analysis = frame_analysis.model_copy(update={"director_plan": duplicate_moment_plan})

    with pytest.raises(ValidationError, match="signature moment ids must be unique"):
        _normalize_private_storyboard_namespace(invalid_analysis)


@pytest.mark.asyncio
async def test_storyboard_v2_normalizes_all_private_id_shapes_without_scrubbing_prose(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()
    original_direct = fake_llm.direct_frame_anchored_video_storyboard
    original_generate = fake_llm.generate_frame_anchored_video_storyboard
    observed_call3_ids: dict[str, object] = {}

    async def mixed_private_namespace_director(*args, **kwargs):
        plan = await original_direct(*args, **kwargs)
        old_beat_id = plan.climax_beats[0].beat_id
        old_window_id = plan.action_arc_windows[0].window_id
        old_moment_id = plan.signature_moment_plan[0].moment_id
        reserved_beat = plan.climax_beats[0].model_copy(
            update={
                "beat_id": "__sbv2_beat_001__",
                "attention_objective": "Preserve a separately reserved private beat.",
            }
        )
        climax_beats = [
            plan.climax_beats[0].model_copy(
                update={
                    "beat_id": " action ",
                    "depends_on": [
                        " action " if dependency == old_beat_id else dependency
                        for dependency in plan.climax_beats[0].depends_on
                    ],
                }
            ),
            reserved_beat,
        ]
        action_arc_windows = [
            window.model_copy(
                update={
                    "window_id": (
                        "camera-action" if window.window_id == old_window_id else window.window_id
                    ),
                    "depends_on": [
                        "camera-action" if dependency == old_window_id else dependency
                        for dependency in window.depends_on
                    ],
                }
            )
            for window in plan.action_arc_windows
        ]
        signature_moment_plan = [
            moment.model_copy(
                update={
                    "moment_id": (
                        "subject" if moment.moment_id == old_moment_id else moment.moment_id
                    ),
                    "source_behavior_beat_ids": [" camera action "],
                    "assigned_beat_id": (
                        " action "
                        if moment.assigned_beat_id == old_beat_id
                        else moment.assigned_beat_id
                    ),
                }
            )
            for moment in plan.signature_moment_plan
        ]
        return plan.model_copy(
            update={
                "climax_beats": climax_beats,
                "action_arc_windows": action_arc_windows,
                "signature_moment_plan": signature_moment_plan,
            }
        )

    async def natural_language_candidate(*args, **kwargs):
        frame_analysis = kwargs["frame_analysis"]
        plan = frame_analysis.director_plan
        assert plan is not None
        signature_moment = plan.signature_moment_plan[0]
        private_ids = {
            "behavior": signature_moment.source_behavior_beat_ids[0],
            "beat": plan.climax_beats[0].beat_id,
            "reserved_beat": plan.climax_beats[1].beat_id,
            "window": plan.action_arc_windows[0].window_id,
            "moment": signature_moment.moment_id,
        }
        observed_call3_ids.update(private_ids)
        storyboard = await original_generate(*args, **kwargs)
        evidence = storyboard.scenes[1].execution_evidence[0]
        storyboard.scenes[1].execution_evidence = [
            evidence.model_copy(update={"claim_id": "primary-claim"}),
            evidence.model_copy(
                update={
                    "claim_id": "__sbv2_claim_001__",
                    "executor_kind": "target_object",
                    "action_or_state_change": "a second target action executes visibly",
                }
            ),
        ]
        natural_sentence = "The camera follows the action while the subject moves."
        storyboard.scenes[1].visual = (
            f"{' '.join(private_ids.values())} primary-claim __sbv2_claim_001__; {natural_sentence}"
        )
        storyboard.scenes[1].motion = natural_sentence
        return storyboard

    fake_llm.direct_frame_anchored_video_storyboard = mixed_private_namespace_director
    fake_llm.generate_frame_anchored_video_storyboard = natural_language_candidate
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="external-ai-storyboard-v2-private-namespace.db",
    )
    try:
        created = client.post(
            "/api/v1/integrations/ai/storyboard-v2",
            headers=_authorized_headers(),
            json=_storyboard_v2_payload(),
        ).json()
        job_id = created["data"]["job_id"]
        polled = client.get(
            f"/api/v1/integrations/ai/jobs/{job_id}",
            headers=_authorized_headers(),
        ).json()["data"]
    finally:
        app.dependency_overrides.clear()
        client.close()

    assert polled["status"] == "succeeded"
    natural_sentence = "The camera follows the action while the subject moves."
    assert natural_sentence in polled["storyboard_text"]
    assert "__sbv2_" not in polled["storyboard_text"]
    assert "camera action" not in polled["storyboard_text"]
    assert "camera-action" not in polled["storyboard_text"]
    assert "primary-claim" not in polled["storyboard_text"]
    assert "__sbv2_claim_" not in polled["storyboard_text"]
    assert fake_llm.calls == [
        "analyze_video_frame_pair",
        "direct_frame_anchored_video_storyboard",
        "generate_frame_anchored_video_storyboard",
    ]
    assert observed_call3_ids == {
        "behavior": "__sbv2_behavior_001__",
        "beat": "__sbv2_beat_002__",
        "reserved_beat": "__sbv2_beat_001__",
        "window": "__sbv2_window_001__",
        "moment": "__sbv2_moment_001__",
    }

    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        task = await session.get(GenerationTask, job_id)
        assert task is not None
        director_plan = task.metadata_json["frame_analysis"]["director_plan"]
        assert [beat["beat_id"] for beat in director_plan["climax_beats"]] == [
            "__sbv2_beat_002__",
            "__sbv2_beat_001__",
        ]
        assert director_plan["signature_moment_plan"][0]["source_behavior_beat_ids"] == [
            "__sbv2_behavior_001__"
        ]
        assert director_plan["action_arc_windows"][0]["window_id"] == ("__sbv2_window_001__")
        assert director_plan["signature_moment_plan"][0]["moment_id"] == ("__sbv2_moment_001__")
        assert director_plan["signature_moment_plan"][0]["assigned_beat_id"] == (
            "__sbv2_beat_002__"
        )
        candidate = task.metadata_json["frame_anchored_storyboard_candidate"]
        assert [
            evidence["claim_id"] for evidence in candidate["scenes"][1]["execution_evidence"]
        ] == ["__sbv2_claim_002__", "__sbv2_claim_001__"]

    await engine.dispose()


def test_claim_alias_replacement_is_longest_single_pass_and_unicode_safe() -> None:
    mapping = {
        "primary-claim": "__sbv2_claim_001__",
        "primary-claim-extra": "__sbv2_claim_002__",
        "first-id": "second-id",
        "second-id": "third-id",
    }

    normalized = _replace_private_id_aliases(
        {
            "claim_id": "primary-claim-extra",
            "visual": (
                "执行primary-claim动作；動作primary-claim-extra完了；primary-claimant 保持原样。"
            ),
            "motion": "first-id",
        },
        mapping,
    )

    assert normalized["claim_id"] == "__sbv2_claim_002__"
    assert normalized["visual"] == (
        "执行__sbv2_claim_001__动作；動作__sbv2_claim_002__完了；primary-claimant 保持原样。"
    )
    assert normalized["motion"] == "second-id"


def test_formatter_scrubs_canonical_claim_ids_adjacent_to_unicode_letters() -> None:
    canonical_claim_id = "__sbv2_claim_001__"
    natural_sentence = "primary-claimant 是普通自然语言，不是完整 private ID。"
    storyboard = FrameAnchoredStoryboard(
        duration_seconds=4,
        aspect_ratio="9:16",
        scenes=[
            FrameAnchoredStoryboardScene(
                scene_index=1,
                start_second=0,
                end_second=1,
                frame_anchor="first_frame",
                visual="Opening anchor.",
            ),
            FrameAnchoredStoryboardScene(
                scene_index=2,
                start_second=1,
                end_second=4,
                frame_anchor="last_frame",
                visual=f"执行{canonical_claim_id}动作；{natural_sentence}",
                motion=f"動作{canonical_claim_id}完了",
                execution_evidence=[
                    {
                        "claim_id": canonical_claim_id,
                        "executor_kind": "target_subject",
                        "assertion": "affirmed",
                        "action_or_state_change": "linked action changes state visibly",
                        "signature_moment_ids": ["signature-action"],
                        "source_behavior_beat_ids": ["source-behavior"],
                    }
                ],
            ),
        ],
    )

    rendered = _format_frame_anchored_storyboard_text(
        storyboard,
        private_sources=({"claim_id": "primary-claim"},),
    )

    assert canonical_claim_id not in rendered
    assert "执行linked item动作" in rendered
    assert "動作linked item完了" in rendered
    assert natural_sentence in rendered


@pytest.mark.parametrize(
    "claim_pair",
    [
        ("primary-claim", "primary-claim-extra"),
        ("phase:claim", "phase:claim.extra"),
    ],
)
@pytest.mark.parametrize("reverse_evidence_order", [False, True])
@pytest.mark.asyncio
async def test_storyboard_v2_prefix_overlapping_claim_aliases_are_order_independent_and_private(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    claim_pair: tuple[str, str],
    reverse_evidence_order: bool,
) -> None:
    fake_llm = FakeExternalAILLM()
    original_generate = fake_llm.generate_frame_anchored_video_storyboard
    observed_raw_claim_ids: list[str] = []

    async def overlapping_claim_candidate(*args, **kwargs):
        storyboard = await original_generate(*args, **kwargs)
        evidence = storyboard.scenes[1].execution_evidence[0]
        ordered_claim_ids = list(claim_pair)
        if reverse_evidence_order:
            ordered_claim_ids.reverse()
        observed_raw_claim_ids.extend(ordered_claim_ids)
        storyboard.scenes[1].execution_evidence = [
            evidence.model_copy(
                update={
                    "claim_id": claim_id,
                    "executor_kind": ("target_subject" if index == 0 else "target_object"),
                    "action_or_state_change": (f"target action {index + 1} completes visibly"),
                }
            )
            for index, claim_id in enumerate(ordered_claim_ids)
        ]
        short_claim, long_claim = claim_pair
        natural_sentence = "primary-claimant 是普通自然语言，不是完整 private ID。"
        scene = storyboard.scenes[1]
        scene.visual = f"{scene.visual} 中{short_claim}文；{natural_sentence}"
        scene.motion = f"{scene.motion} 動{long_claim}く"
        payoff_scene = storyboard.scenes[2]
        payoff_scene.action_result_requirement = (
            f"{payoff_scene.action_result_requirement or ''} 甲{long_claim}乙"
        )
        scene.effect_timing = f"{scene.effect_timing or ''} 丙{short_claim}丁"
        final_scene = storyboard.scenes[-1]
        final_scene.notes = f"{final_scene.notes or ''} 收__sbv2_claim_001__束"
        return storyboard

    fake_llm.generate_frame_anchored_video_storyboard = overlapping_claim_candidate
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    filename = (
        "storyboard-v2-overlapping-claims-"
        f"{claim_pair[0].replace(':', '-').replace('.', '-')}-"
        f"{'reverse' if reverse_evidence_order else 'forward'}.db"
    )
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename=filename,
    )
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        service = ExternalAIGenerationService()
        async with session_factory() as session:
            task = GenerationTask(
                queue_name="text_queue",
                task_type="external_video_storyboard_v2",
                business_type="external_ai",
                business_id=filename,
                payload_json=_storyboard_v2_payload(),
                queued_at=utcnow(),
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            result = await service.execute_frame_anchored_video_storyboard(session, task)
            task.status = "succeeded"
            task.result_json = result
            session.add(task)
            await session.commit()
            job_id = task.id

        polled = client.get(
            f"/api/v1/integrations/ai/jobs/{job_id}",
            headers=_authorized_headers(),
        ).json()["data"]

        assert polled["status"] == "succeeded"
        storyboard_text = polled["storyboard_text"]
        short_claim, long_claim = claim_pair
        assert f"中{short_claim}文" not in storyboard_text
        assert f"動{long_claim}く" not in storyboard_text
        assert f"甲{long_claim}乙" not in storyboard_text
        assert f"丙{short_claim}丁" not in storyboard_text
        assert "中linked item文" in storyboard_text
        assert "動linked itemく" in storyboard_text
        assert "甲linked item乙" in storyboard_text
        assert "丙linked item丁" in storyboard_text
        assert "收linked item束" in storyboard_text
        assert "__sbv2_claim_" not in storyboard_text
        assert "primary-claimant 是普通自然语言，不是完整 private ID。" in storyboard_text
        assert fake_llm.calls == [
            "analyze_video_frame_pair",
            "direct_frame_anchored_video_storyboard",
            "generate_frame_anchored_video_storyboard",
        ]

        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            task = await session.get(GenerationTask, job_id)
            assert task is not None
            candidate = task.metadata_json["frame_anchored_storyboard_candidate"]
            normalized_storyboard = FrameAnchoredStoryboard.model_validate(candidate)
            normalized_claim_ids = [
                evidence.claim_id for evidence in normalized_storyboard.scenes[1].execution_evidence
            ]
            assert normalized_claim_ids == [
                "__sbv2_claim_001__",
                "__sbv2_claim_002__",
            ]
            assert observed_raw_claim_ids == list(
                reversed(claim_pair) if reverse_evidence_order else claim_pair
            )
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("primary-claim-extra", "primary-claim-extra"),
        ("primary-claim:extra", "primary-claim:extra"),
        ("primary-claim.extra", "primary-claim.extra"),
        ("primary-claim_extra", "primary-claim_extra"),
        ("prefix-primary-claim", "prefix-primary-claim"),
        ("prefix:primary-claim", "prefix:primary-claim"),
        ("prefix.primary-claim", "prefix.primary-claim"),
        ("prefix_primary-claim", "prefix_primary-claim"),
        ("prefix.primary-claim_extra", "prefix.primary-claim_extra"),
        ("\u4e2dprimary-claim\u6587", "\u4e2d__sbv2_claim_001__\u6587"),
        ("\u52d5primary-claim\u304f", "\u52d5__sbv2_claim_001__\u304f"),
        ("(primary-claim)", "(__sbv2_claim_001__)"),
        ("primary-claim,", "__sbv2_claim_001__,"),
        ("primary-claim;", "__sbv2_claim_001__;"),
        ("primary-claim\u3002", "__sbv2_claim_001__\u3002"),
        ("primary-claim. next", "__sbv2_claim_001__. next"),
        ("primary-claim.", "__sbv2_claim_001__."),
        ("primary-claim", "__sbv2_claim_001__"),
    ],
)
def test_raw_private_id_replacement_respects_complete_safe_token_boundaries(
    source: str,
    expected: str,
) -> None:
    assert (
        _replace_private_ids_in_text(
            source,
            {"primary-claim": "__sbv2_claim_001__"},
        )
        == expected
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("__sbv2_claim_001__-extra", "__sbv2_claim_001__-extra"),
        ("__sbv2_claim_001__:extra", "__sbv2_claim_001__:extra"),
        ("__sbv2_claim_001__.extra", "__sbv2_claim_001__.extra"),
        ("__sbv2_claim_001___extra", "__sbv2_claim_001___extra"),
        ("prefix-__sbv2_claim_001__", "prefix-__sbv2_claim_001__"),
        ("prefix:__sbv2_claim_001__", "prefix:__sbv2_claim_001__"),
        ("prefix.__sbv2_claim_001__", "prefix.__sbv2_claim_001__"),
        ("prefix___sbv2_claim_001__", "prefix___sbv2_claim_001__"),
        (
            "prefix.__sbv2_claim_001__-extra",
            "prefix.__sbv2_claim_001__-extra",
        ),
        ("\u4e2d__sbv2_claim_001__\u6587", "\u4e2dlinked item\u6587"),
        ("\u52d5__sbv2_claim_001__\u304f", "\u52d5linked item\u304f"),
        ("(__sbv2_claim_001__)", "(linked item)"),
        ("__sbv2_claim_001__,", "linked item,"),
        ("__sbv2_claim_001__;", "linked item;"),
        ("__sbv2_claim_001__\u3002", "linked item\u3002"),
        ("__sbv2_claim_001__. next", "linked item. next"),
        ("__sbv2_claim_001__.", "linked item."),
        ("__sbv2_claim_001__", "linked item"),
    ],
)
def test_canonical_private_id_scrub_respects_complete_safe_token_boundaries(
    source: str,
    expected: str,
) -> None:
    assert (
        _replace_private_ids_in_text(
            source,
            {"__sbv2_claim_001__": "linked item"},
        )
        == expected
    )


@pytest.mark.asyncio
async def test_storyboard_v2_polling_preserves_unregistered_longer_private_id_tokens(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llm = FakeExternalAILLM()
    original_generate = fake_llm.generate_frame_anchored_video_storyboard
    raw_longer_tokens = [
        "primary-claim-extra",
        "primary-claim:extra",
        "primary-claim.extra",
        "primary-claim_extra",
        "prefix-primary-claim",
        "prefix:primary-claim",
        "prefix.primary-claim",
        "prefix_primary-claim",
        "prefix.primary-claim_extra",
    ]
    canonical_longer_tokens = [
        "__sbv2_claim_001__-extra",
        "__sbv2_claim_001__:extra",
        "__sbv2_claim_001__.extra",
        "__sbv2_claim_001___extra",
        "prefix-__sbv2_claim_001__",
        "prefix:__sbv2_claim_001__",
        "prefix.__sbv2_claim_001__",
        "prefix___sbv2_claim_001__",
        "prefix.__sbv2_claim_001__-extra",
    ]

    async def token_boundary_candidate(*args, **kwargs):
        storyboard = await original_generate(*args, **kwargs)
        evidence = storyboard.scenes[1].execution_evidence[0]
        storyboard.scenes[1].execution_evidence = [
            evidence.model_copy(update={"claim_id": "primary-claim"})
        ]
        storyboard.scenes[1].visual = (
            f"{storyboard.scenes[1].visual} keep {' '.join(raw_longer_tokens)}; "
            "scrub \u4e2dprimary-claim\u6587."
        )
        storyboard.scenes[-1].notes = (
            f"{storyboard.scenes[-1].notes or ''} "
            f"keep {' '.join(canonical_longer_tokens)}; "
            "scrub \u6536__sbv2_claim_001__\u675f."
        )
        return storyboard

    fake_llm.generate_frame_anchored_video_storyboard = token_boundary_candidate
    monkeypatch.setattr(external_ai_service_module, "get_llm_provider", lambda: fake_llm)
    client, engine, app = await _client_with_db(
        tmp_path,
        monkeypatch,
        filename="storyboard-v2-private-id-token-boundaries.db",
    )
    try:
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        service = ExternalAIGenerationService()
        async with session_factory() as session:
            task = GenerationTask(
                queue_name="text_queue",
                task_type="external_video_storyboard_v2",
                business_type="external_ai",
                business_id="private-id-token-boundaries",
                payload_json=_storyboard_v2_payload(),
                queued_at=utcnow(),
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            result = await service.execute_frame_anchored_video_storyboard(session, task)
            task.status = "succeeded"
            task.result_json = result
            session.add(task)
            await session.commit()
            job_id = task.id

        polled = client.get(
            f"/api/v1/integrations/ai/jobs/{job_id}",
            headers=_authorized_headers(),
        ).json()["data"]

        assert polled["status"] == "succeeded"
        storyboard_text = polled["storyboard_text"]
        for token in (*raw_longer_tokens, *canonical_longer_tokens):
            assert token in storyboard_text
        assert "\u4e2dprimary-claim\u6587" not in storyboard_text
        assert "\u6536__sbv2_claim_001__\u675f" not in storyboard_text
        assert "\u4e2dlinked item\u6587" in storyboard_text
        assert "\u6536linked item\u675f" in storyboard_text
        assert fake_llm.calls == [
            "analyze_video_frame_pair",
            "direct_frame_anchored_video_storyboard",
            "generate_frame_anchored_video_storyboard",
        ]

        async with session_factory() as session:
            task = await session.get(GenerationTask, job_id)
            assert task is not None
            candidate = FrameAnchoredStoryboard.model_validate(
                task.metadata_json["frame_anchored_storyboard_candidate"]
            )
            assert candidate.scenes[1].execution_evidence[0].claim_id == ("__sbv2_claim_001__")
    finally:
        app.dependency_overrides.clear()
        client.close()
        await engine.dispose()
