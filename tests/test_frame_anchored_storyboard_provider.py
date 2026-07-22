import json
import logging

import httpx
import pytest
from pydantic import ValidationError

from backend.app.core.errors import ProviderError
from backend.app.integrations.llm.responses_provider import GatewayResponsesLLMProvider
from backend.app.schemas.ai import (
    DirectorBeat,
    FrameAnalysis,
    FrameAnchoredDirectorPlan,
    FrameAnchoredStoryboard,
    FrameAnchoredStoryboardScene,
    FrameLanguageAnalysis,
    FrameTransitionBrief,
    FrameVisualFacts,
    ReferenceVideoFrame,
    validate_director_coverage,
)

FIRST_FRAME_URL = "https://cdn.example.test/first.png"
LAST_FRAME_URL = "https://cdn.example.test/last.png"


def test_director_plan_rejects_climax_without_causal_evidence() -> None:
    with pytest.raises(ValidationError, match="climax beat requires source evidence"):
        FrameAnchoredDirectorPlan.model_validate(
            {
                "narrative_objective": "Build to a decisive result.",
                "attention_path": ["subject", "result"],
                "tension_curve": ["setup", "climax", "resolution"],
                "climax_beats": [
                    {
                        "beat_id": "impact",
                        "stage": "climax",
                        "source_evidence": [],
                    }
                ],
                "anchor_adaptation_plan": ["End in the supplied last frame."],
                "anti_flattening_constraints": ["Do not merge impact and result."],
            }
        )


def test_director_coverage_requires_frame_anchored_storyboard_to_represent_climax() -> None:
    director_plan = FrameAnchoredDirectorPlan(
        narrative_objective="Build to a decisive result.",
        attention_path=["subject", "impact", "result"],
        tension_curve=["setup", "climax", "resolution"],
        climax_beats=[
            DirectorBeat(
                beat_id="impact",
                stage="climax",
                source_evidence=["The reference motion culminates in an observable impact."],
                importance="core",
            )
        ],
        anchor_adaptation_plan=["End in the supplied last frame."],
        anti_flattening_constraints=["Do not merge impact and result."],
    )
    storyboard = FrameAnchoredStoryboard(
        duration_seconds=12,
        aspect_ratio="9:16",
        scenes=[
            FrameAnchoredStoryboardScene(
                scene_index=1,
                frame_anchor="first_frame",
                visual="Open in the supplied first-frame composition.",
                cinematic_beat="setup",
            ),
            FrameAnchoredStoryboardScene(
                scene_index=2,
                frame_anchor="last_frame",
                visual="Resolve in the supplied last-frame composition.",
                cinematic_beat="resolution",
            ),
        ],
    )

    with pytest.raises(ValueError, match="required director beat: impact"):
        validate_director_coverage(storyboard, director_plan)


def test_director_coverage_assigns_a_missing_core_beat_to_its_timeline_scene() -> None:
    director_plan = FrameAnchoredDirectorPlan(
        narrative_objective="Escalate to an evidence-backed visible impact.",
        attention_path=["subject", "impact", "result"],
        tension_curve=["setup", "trigger", "escalation", "climax", "resolution"],
        climax_beats=[
            DirectorBeat(
                beat_id="impact_reveal",
                stage="climax",
                source_evidence=["The supplied frames establish an activation and result."],
                start_ratio=0.35,
                end_ratio=0.75,
                importance="core",
            )
        ],
        anchor_adaptation_plan=["Resolve in the supplied last frame."],
        anti_flattening_constraints=["Keep the causal impact readable."],
    )
    storyboard = FrameAnchoredStoryboard(
        duration_seconds=10,
        aspect_ratio="9:16",
        scenes=[
            FrameAnchoredStoryboardScene(
                scene_index=1,
                start_second=0,
                end_second=3,
                frame_anchor="first_frame",
                visual="Open on the supplied first-frame composition.",
            ),
            FrameAnchoredStoryboardScene(
                scene_index=2,
                start_second=3,
                end_second=8,
                frame_anchor="transition",
                visual="Show the adapted causal action and visible impact.",
            ),
            FrameAnchoredStoryboardScene(
                scene_index=3,
                start_second=8,
                end_second=10,
                frame_anchor="last_frame",
                visual="Resolve on the supplied last-frame composition.",
            ),
        ],
    )

    validate_director_coverage(storyboard, director_plan)

    assert storyboard.scenes[1].cinematic_beat == "impact_reveal"


@pytest.mark.asyncio
async def test_gateway_director_plan_uses_target_frames_and_evidence_analysis() -> None:
    provider, captured = _gateway_provider_with_responses(
        {
            "narrative_objective": "Escalate observed action into a decisive visible result.",
            "attention_path": ["opening subject", "causal action", "visible result"],
            "tension_curve": ["setup", "trigger", "escalation", "climax", "resolution"],
            "climax_beats": [
                {
                    "beat_id": "decisive_result",
                    "stage": "climax",
                    "source_evidence": [
                        "The reference shows a causal action followed by a result."
                    ],
                    "start_ratio": 0.45,
                    "end_ratio": 0.72,
                    "attention_objective": "Hold attention on the result of the action.",
                    "camera_instruction": "Use an in-shot viewpoint change to sharpen the impact.",
                    "action_requirement": "Show the action before its visible result.",
                    "effect_requirement": "Peak the observed effect at the causal result.",
                    "importance": "core",
                }
            ],
            "overlay_lifecycle_plan": [],
            "anchor_adaptation_plan": ["Resolve to the exact supplied last-frame composition."],
            "anti_flattening_constraints": [
                "Do not collapse trigger, action, impact, and resolution into one flat move."
            ],
        }
    )

    plan = await provider.direct_frame_anchored_video_storyboard(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        _analysis(),
        12,
        "9:16",
    )

    assert plan.climax_beats[0].beat_id == "decisive_result"
    content = captured[0]["input"][1]["content"]
    assert content[1] == {"type": "input_image", "image_url": FIRST_FRAME_URL}
    assert content[3] == {"type": "input_image", "image_url": LAST_FRAME_URL}
    assert '"frame_analysis"' in content[0]["text"]
    system_prompt = captured[0]["input"][0]["content"].lower()
    assert "observed evidence" in system_prompt
    assert "director inference" in system_prompt
    assert "post-production" in system_prompt
    assert "identity" in system_prompt
    assert "attention_path must be a list of plain strings" in system_prompt
    assert "anchor_adaptation_plan must be a list of plain strings" in system_prompt


@pytest.mark.asyncio
async def test_gateway_frame_analysis_sends_first_then_last_image() -> None:
    provider, captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "transition_brief": {
                "shared_visual_facts": ["shared subject"],
                "continuity_requirements": ["preserve the supplied frames"],
                "visual_transition": "camera follows the subject",
                "narrative_arc": "opening to ending",
            },
            "language_analysis": {
                "first_frame_visible_languages": ["en"],
                "last_frame_visible_languages": ["en"],
                "recommended_output_language": "en",
                "reason": "Visible text is English.",
            },
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        12,
        "9:16",
    )

    assert analysis.language_analysis.recommended_output_language == "en"
    content = captured[0]["input"][1]["content"]
    assert content[0]["type"] == "input_text"
    assert "FIRST FRAME" in content[0]["text"]
    assert content[1] == {"type": "input_image", "image_url": FIRST_FRAME_URL}
    assert content[2]["type"] == "input_text"
    assert "LAST FRAME" in content[2]["text"]
    assert content[3] == {"type": "input_image", "image_url": LAST_FRAME_URL}
    system_prompt = captured[0]["input"][0]["content"]
    _assert_no_legacy_content(system_prompt)

@pytest.mark.asyncio
async def test_gateway_frame_anchored_operations_use_long_timeout() -> None:
    captured_timeouts: list[dict[str, float | None]] = []
    response_texts = [
        json.dumps(_analysis().model_dump(mode="json")),
        json.dumps(
            {
                "duration_seconds": 12,
                "aspect_ratio": "9:16",
                "scenes": [
                    {
                        "scene_index": 1,
                        "start_second": 0,
                        "end_second": 6,
                        "frame_anchor": "first_frame",
                        "visual": "Start from the supplied first frame.",
                    },
                    {
                        "scene_index": 2,
                        "start_second": 6,
                        "end_second": 12,
                        "frame_anchor": "last_frame",
                        "visual": "End on the supplied last frame.",
                    },
                ],
            }
        ),
    ]

    async def handler(request: httpx.Request) -> httpx.Response:
        captured_timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json={"output_text": response_texts.pop(0)})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://model.example.test/v1",
        timeout=None,
    ) as client:
        provider = GatewayResponsesLLMProvider(
            api_key="gateway-key",
            base_url="https://model.example.test/v1",
            model="gpt-5.5",
            timeout_seconds=180,
            fast_timeout_seconds=45,
            http_client=client,
        )
        analysis = await provider.analyze_video_frame_pair(
            FIRST_FRAME_URL,
            LAST_FRAME_URL,
            12,
            "9:16",
        )
        storyboard = await provider.generate_frame_anchored_video_storyboard(
            FIRST_FRAME_URL,
            LAST_FRAME_URL,
            analysis,
            12,
            "9:16",
        )

    assert len(storyboard.scenes) == 2
    assert captured_timeouts == [
        {"connect": 5.0, "read": 180.0, "write": 10.0, "pool": 5.0},
        {"connect": 5.0, "read": 180.0, "write": 10.0, "pool": 5.0},
    ]

@pytest.mark.asyncio
async def test_gateway_joint_analysis_sends_reference_frames_chronologically() -> None:
    provider, captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "transition_brief": {
                "shared_visual_facts": ["shared subject"],
                "continuity_requirements": ["preserve target frame truth"],
                "visual_transition": "adapt the reference motion",
                "narrative_arc": "opening to ending",
            },
            "language_analysis": {
                "first_frame_visible_languages": [],
                "last_frame_visible_languages": [],
                "recommended_output_language": "en",
                "reason": "No target-frame text requires another language.",
            },
            "reference_video_analysis": _reference_video_analysis_data(),
        }
    )
    reference_frames = [
        ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA"),
        ReferenceVideoFrame(timestamp_seconds=2, image_url="data:image/jpeg;base64,BBB"),
        ReferenceVideoFrame(timestamp_seconds=5.8, image_url="data:image/jpeg;base64,CCC"),
    ]

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        12,
        "9:16",
        reference_frames=reference_frames,
        reference_video_duration_seconds=5.8,
        reference_video_sample_interval_seconds=2,
    )

    assert analysis.reference_video_analysis is not None
    assert analysis.reference_video_analysis.adapted_constraints.subject_presence.strength == (
        "preferred"
    )
    assert analysis.reference_video_analysis.segments[0].subject_presence.appearance == (
        "The subject enters from the right edge behind foreground light."
    )
    assert analysis.reference_video_analysis.segments[0].subject_presence.action == (
        "The subject walks to center, turns toward the camera, and raises the target object."
    )
    assert analysis.reference_video_analysis.segments[0].subject_presence.interaction == (
        "The gesture intensifies the surrounding gold particles."
    )
    content = captured[0]["input"][1]["content"]
    assert content[1] == {"type": "input_image", "image_url": FIRST_FRAME_URL}
    assert content[3] == {"type": "input_image", "image_url": LAST_FRAME_URL}
    assert "REFERENCE FRAME 0.00s" in content[4]["text"]
    assert content[5] == {"type": "input_image", "image_url": reference_frames[0].image_url}
    assert "REFERENCE FRAME 2.00s" in content[6]["text"]
    assert content[7] == {"type": "input_image", "image_url": reference_frames[1].image_url}
    assert "REFERENCE FRAME 5.80s" in content[8]["text"]
    assert content[9] == {"type": "input_image", "image_url": reference_frames[2].image_url}
    system_prompt = captured[0]["input"][0]["content"]
    assert "subject_presence" in system_prompt.lower()
    assert "behavior graph" in system_prompt.lower()
    assert "dependencies" in system_prompt.lower()
    assert "target first and last frames" in system_prompt.lower()
    assert "camera" in system_prompt.lower()
    assert "preferred" in system_prompt.lower()
    assert "visual_identity_mappings" in system_prompt.lower()
    assert "replace_with_target" in system_prompt.lower()
    assert "morph_to_target" in system_prompt.lower()
    assert "preserve_through_last_anchor" in system_prompt.lower()
    assert "final overlay" in system_prompt.lower()
    assert "diamond" not in system_prompt.lower()
    for excluded in ("audio", "speech", "lyrics", "voiceover", "transcript"):
        assert excluded in system_prompt.lower()


@pytest.mark.asyncio
async def test_gateway_frame_analysis_normalizes_observed_reference_text_shape() -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "reference_video_analysis": {
                "duration_seconds": 10.0,
                "sample_interval_seconds": 2.0,
                "segments": [
                    {
                        "start_second": 0.0,
                        "end_second": 2.0,
                        "subject_presence": {
                            "state": "The person remains visible in a medium full-body shot.",
                            "appearance": (
                                "The person enters from the right edge behind foreground light."
                            ),
                            "action": (
                                "The person walks to center, turns to the camera, and raises "
                                "the target object."
                            ),
                            "interaction": (
                                "The gesture intensifies the surrounding gold particles."
                            ),
                        },
                        "camera": "A gentle forward push follows the person.",
                        "transition": "Continuous movement carries into the next beat.",
                        "effects": "Soft light trails accent the motion.",
                        "confidence": "high",
                    }
                ],
                "adapted_constraints": {
                    "subject_presence": "Keep the target person present for most of the video.",
                    "camera_pattern": (
                        "Prefer a gentle forward push where the target frames allow it."
                    ),
                    "transition_pattern": "Prefer continuous movement between beats.",
                    "effects_pattern": (
                        "Prefer restrained light-trail effects without copying reference content."
                    ),
                },
            },
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        10,
        "9:16",
        reference_frames=[
            ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA")
        ],
        reference_video_duration_seconds=10,
        reference_video_sample_interval_seconds=2,
    )

    reference = analysis.reference_video_analysis
    assert reference is not None
    assert reference.segments[0].subject_presence.state == (
        "The person remains visible in a medium full-body shot."
    )
    assert reference.segments[0].subject_presence.appearance == (
        "The person enters from the right edge behind foreground light."
    )
    assert reference.segments[0].subject_presence.action == (
        "The person walks to center, turns to the camera, and raises the target object."
    )
    assert reference.segments[0].subject_presence.interaction == (
        "The gesture intensifies the surrounding gold particles."
    )
    assert reference.segments[0].camera.movement == "A gentle forward push follows the person."
    assert reference.segments[0].transition.description == (
        "Continuous movement carries into the next beat."
    )
    assert reference.segments[0].effects == ["Soft light trails accent the motion."]
    assert reference.adapted_constraints.subject_presence.strength == "preferred"
    assert reference.adapted_constraints.camera_pattern.strength == "preferred"
    assert reference.adapted_constraints.transition_pattern.strength == "preferred"
    assert reference.adapted_constraints.effects_pattern.strength == "preferred"


@pytest.mark.asyncio
async def test_gateway_frame_analysis_emits_reference_identity_mapping_plan() -> None:
    reference_analysis = _reference_video_analysis_data()
    reference_analysis["visual_identity_mappings"] = [
        {
            "reference_element": "Gold x200,000 reward text over the shattered diamond.",
            "element_type": "reward",
            "strategy": "replace_with_target",
            "target_first_frame_equivalent": None,
            "target_last_frame_equivalent": "Gold x50,000 reward text in the supplied last frame.",
            "instruction": (
                "Keep the reference reward reveal timing and gold burst, but show the exact "
                "target last-frame reward value at the ending anchor."
            ),
        }
    ]
    provider, captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "transition_brief": {
                "shared_visual_facts": ["golden subject"],
                "continuity_requirements": ["begin and end at the supplied images"],
                "visual_transition": "A sword strike resolves into the ending reward frame.",
                "narrative_arc": "activation to reward reveal",
            },
            "language_analysis": {
                "first_frame_visible_languages": [],
                "last_frame_visible_languages": ["en"],
                "recommended_output_language": "en",
                "reason": "The ending reward text is English.",
            },
            "reference_video_analysis": reference_analysis,
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        12,
        "9:16",
        reference_frames=[
            ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA")
        ],
        reference_video_duration_seconds=5.8,
        reference_video_sample_interval_seconds=2,
    )

    assert analysis.reference_video_analysis is not None
    mapping = analysis.reference_video_analysis.visual_identity_mappings[0]
    assert mapping.strategy == "replace_with_target"
    assert mapping.target_last_frame_equivalent == (
        "Gold x50,000 reward text in the supplied last frame."
    )
    system_prompt = captured[0]["input"][0]["content"].lower()
    assert "visual_identity_mappings" in system_prompt
    assert "replace_with_target" in system_prompt
    assert "morph_to_target" in system_prompt

@pytest.mark.asyncio
async def test_gateway_frame_analysis_keeps_behavior_graph_and_final_overlay_mapping() -> None:
    reference_data = _reference_video_analysis_data()
    reference_data["visual_identity_mappings"] = [
        {
            "reference_element": "reward panel with a visible value",
            "element_type": "reward",
            "strategy": "preserve_through_last_anchor",
            "target_first_frame_equivalent": None,
            "target_last_frame_equivalent": None,
            "instruction": "Keep the panel readable through the generated final frame.",
        }
    ]
    reference_data["behavior_graph"] = {
        "entities": ["performer", "tool", "target", "reward panel"],
        "beats": [
            {
                "beat_id": "approach",
                "reference_start_second": 0,
                "reference_end_second": 2,
                "description": "The performer approaches the target with the tool.",
                "visible_evidence": ["performer", "tool", "target"],
                "importance": "core",
                "minimum_readable_duration_seconds": 1,
            },
            {
                "beat_id": "result_overlay",
                "reference_start_second": 4,
                "reference_end_second": 6,
                "description": "The reward panel appears after the result and stays visible.",
                "visible_evidence": ["reward panel"],
                "behavior_type": "overlay",
                "importance": "supporting",
                "minimum_readable_duration_seconds": 1,
                "depends_on": ["approach"],
                "must_remain_visible_until_final": True,
                "locked_text": "x200,000",
            },
        ],
    }
    provider, _captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "transition_brief": {},
            "language_analysis": {},
            "reference_video_analysis": reference_data,
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        6,
        "9:16",
        reference_frames=[
            ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA")
        ],
        reference_video_duration_seconds=6,
        reference_video_sample_interval_seconds=2,
    )

    reference = analysis.reference_video_analysis
    assert reference is not None
    assert reference.visual_identity_mappings[0].strategy == "preserve_through_last_anchor"
    assert reference.behavior_graph is not None
    assert reference.behavior_graph.beats[1].must_remain_visible_until_final is True
    assert reference.behavior_graph.beats[1].locked_text == "x200,000"
    assert reference.behavior_graph.beats[1].depends_on == ["beat_001"]


@pytest.mark.asyncio
async def test_gateway_frame_analysis_canonicalizes_malformed_reference_behavior_timeline() -> None:
    reference_data = _reference_video_analysis_data()
    reference_data["behavior_graph"] = {
        "entities": ["performer", "target", "reward"],
        "beats": [
            {
                "beat_id": "intro",
                "reference_start_second": 0,
                "reference_end_second": 2,
                "description": "The performer enters the frame.",
                "visible_evidence": ["performer"],
                "importance": "core",
            },
            {
                "beat_id": "impact",
                "reference_start_second": 2,
                "reference_end_second": 2,
                "description": "The central action reaches its impact.",
                "visible_evidence": ["target reaction"],
                "depends_on": ["intro"],
                "importance": "core",
            },
            {
                "beat_id": "",
                "reference_start_second": "final moment",
                "reference_end_second": "final moment",
                "description": "The reward overlay becomes readable.",
                "visible_evidence": ["reward"],
                "behavior_type": "overlay",
                "depends_on": ["missing", "impact"],
                "must_remain_visible_until_final": True,
                "locked_text": "x200,000",
            },
            {
                "beat_id": "final",
                "reference_start_second": 9,
                "reference_end_second": 9,
                "description": "The final composition settles.",
                "visible_evidence": ["final composition"],
                "depends_on": ["missing"],
            },
        ],
    }
    provider, captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "transition_brief": {},
            "language_analysis": {},
            "reference_video_analysis": reference_data,
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        6,
        "9:16",
        reference_frames=[
            ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA")
        ],
        reference_video_duration_seconds=5.8,
        reference_video_sample_interval_seconds=2,
    )

    reference = analysis.reference_video_analysis
    assert reference is not None
    assert reference.behavior_graph is not None
    beats = reference.behavior_graph.beats
    assert [beat.beat_id for beat in beats] == [
        "beat_001",
        "beat_002",
        "beat_003",
        "beat_004",
    ]
    assert beats[0].depends_on == []
    assert beats[1].depends_on == ["beat_001"]
    assert beats[2].depends_on == ["beat_002"]
    assert beats[3].depends_on == ["beat_003"]
    assert all(
        0 <= beat.reference_start_second < beat.reference_end_second <= 5.8
        for beat in beats
    )
    assert beats[2].must_remain_visible_until_final is True
    assert beats[2].locked_text == "x200,000"
    system_prompt = captured[0]["input"][0]["content"].lower()
    assert "strictly greater than reference_start_second" in system_prompt
    assert "known beat_id" in system_prompt

@pytest.mark.asyncio
async def test_gateway_frame_analysis_accepts_chronological_segments_alias() -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "reference_video_analysis": {
                "duration_seconds": 10.0,
                "sample_interval_seconds": 2.0,
                "chronological_segments": _reference_video_analysis_data()["segments"],
                "adapted_constraints": _reference_video_analysis_data()["adapted_constraints"],
            },
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        10,
        "9:16",
        reference_frames=[
            ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA")
        ],
        reference_video_duration_seconds=10,
        reference_video_sample_interval_seconds=2,
    )

    reference = analysis.reference_video_analysis
    assert reference is not None
    assert len(reference.segments) == 1
    assert reference.segments[0].start_second == 0
    assert reference.segments[0].end_second == 2


@pytest.mark.asyncio
async def test_gateway_frame_analysis_rejects_reference_analysis_without_segments() -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "reference_video_analysis": {
                "duration_seconds": 10.0,
                "sample_interval_seconds": 2.0,
                "segments": [],
                "adapted_constraints": _reference_video_analysis_data()["adapted_constraints"],
            },
        }
    )

    with pytest.raises(ProviderError, match="invalid frame analysis JSON"):
        await provider.analyze_video_frame_pair(
            FIRST_FRAME_URL,
            LAST_FRAME_URL,
            10,
            "9:16",
            reference_frames=[
                ReferenceVideoFrame(timestamp_seconds=0, image_url="data:image/jpeg;base64,AAA")
            ],
            reference_video_duration_seconds=10,
            reference_video_sample_interval_seconds=2,
        )


@pytest.mark.asyncio
async def test_gateway_frame_analysis_normalizes_real_semi_flat_response_shape() -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "version": "V2",
            "video_metadata": {"duration_seconds": 12, "aspect_ratio": "9:16"},
            "first_frame": {
                **_visual_facts("opening state"),
                "visible_text": [
                    {
                        "text": "GCD",
                        "location": "Front of red shirt",
                        "certainty": "partial/visible letters only",
                    }
                ],
            },
            "last_frame": _visual_facts("ending state"),
            "shared_visual_facts": ["Both frames relate to fashion."],
            "continuity_requirements": ["Maintain the fashion-focused visual theme."],
            "plausible_visual_transition": "The subject changes outfits while walking.",
            "narrative_arc": "Opening fashion look transforms into ending fashion look.",
            "visible_languages": [
                {
                    "language": "English",
                    "evidence": "Partial Latin letters on the red shirt.",
                }
            ],
            "recommended_output_language": "English",
            "recommended_output_language_reason": "The supplied frame shows English text.",
        }
    )

    analysis = await provider.analyze_video_frame_pair(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        12,
        "9:16",
    )

    assert analysis.first_frame.visible_text == ["GCD"]
    assert analysis.transition_brief.visual_transition == (
        "The subject changes outfits while walking."
    )
    assert analysis.language_analysis.first_frame_visible_languages == ["English"]
    assert analysis.language_analysis.last_frame_visible_languages == ["English"]
    assert analysis.language_analysis.recommended_output_language == "English"
    assert analysis.language_analysis.reason == "The supplied frame shows English text."


@pytest.mark.asyncio
async def test_gateway_frame_analysis_logs_sanitized_reference_validation_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "first_frame": _visual_facts("opening state"),
            "last_frame": _visual_facts("ending state"),
            "reference_video_analysis": {
                "duration_seconds": 5.8,
                "sample_interval_seconds": 2,
                "segments": [],
                "unexpected_image": "data:image/jpeg;base64,DO_NOT_LOG_THIS",
            },
        }
    )

    with caplog.at_level(logging.WARNING), pytest.raises(
        ProviderError, match="invalid frame analysis JSON"
    ):
        await provider.analyze_video_frame_pair(
            FIRST_FRAME_URL,
            LAST_FRAME_URL,
            12,
            "9:16",
            reference_frames=[
                ReferenceVideoFrame(
                    timestamp_seconds=0,
                    image_url="data:image/jpeg;base64,ALSO_DO_NOT_LOG_THIS",
                )
            ],
            reference_video_duration_seconds=5.8,
            reference_video_sample_interval_seconds=2,
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "Frame analysis JSON validation failed" in messages
    assert "reference_video_analysis.adapted_constraints" in messages
    assert "response_shape" in messages
    assert "data:image" not in messages
    assert "DO_NOT_LOG_THIS" not in messages


@pytest.mark.asyncio
async def test_gateway_storyboard_logs_sanitized_validation_details(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "duration_seconds": 12,
            "aspect_ratio": "9:16",
            "unexpected_image": "data:image/jpeg;base64,DO_NOT_LOG_THIS",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 6,
                    "frame_anchor": "opening",
                    "visual": "Start from the supplied first frame.",
                },
                {
                    "scene_index": 2,
                    "start_second": 6,
                    "end_second": 12,
                    "frame_anchor": "last_frame",
                    "visual": "End on the supplied last frame.",
                },
            ],
        }
    )

    with caplog.at_level(logging.WARNING), pytest.raises(
        ProviderError, match="invalid frame-anchored storyboard JSON"
    ):
        await provider.generate_frame_anchored_video_storyboard(
            FIRST_FRAME_URL,
            LAST_FRAME_URL,
            _analysis(),
            12,
            "9:16",
        )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "Frame-anchored storyboard JSON validation failed" in messages
    assert "scenes.0.frame_anchor" in messages
    assert "response_shape" in messages
    assert "data:image" not in messages
    assert "DO_NOT_LOG_THIS" not in messages


@pytest.mark.asyncio
async def test_gateway_storyboard_accepts_fractional_target_scene_boundaries() -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "duration_seconds": 10,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 1.5,
                    "frame_anchor": "first_frame",
                    "visual": "Begin from the supplied first-frame base layer.",
                },
                {
                    "scene_index": 2,
                    "start_second": 1.5,
                    "end_second": 8.5,
                    "frame_anchor": "transition",
                    "visual": "Carry out the adapted causal action in target timing.",
                },
                {
                    "scene_index": 3,
                    "start_second": 8.5,
                    "end_second": 10,
                    "frame_anchor": "last_frame",
                    "visual": "End on the supplied last-frame base layer and its required overlay.",
                },
            ],
        }
    )

    storyboard = await provider.generate_frame_anchored_video_storyboard(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        _analysis(),
        10,
        "9:16",
    )

    assert storyboard.scenes[0].end_second == 1.5
    assert storyboard.scenes[1].start_second == 1.5
    assert storyboard.scenes[-1].end_second == 10


@pytest.mark.asyncio
async def test_gateway_storyboard_sends_analysis_and_frame_rules() -> None:
    provider, captured = _gateway_provider_with_responses(
        {
            "duration_seconds": 12,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 3,
                    "frame_anchor": "first_frame",
                    "visual": "Use the exact supplied opening state.",
                    "motion": "Slow push in.",
                    "transition_goal": "Begin from first frame.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["soft room tone"],
                    "notes": None,
                },
                {
                    "scene_index": 2,
                    "start_second": 3,
                    "end_second": 9,
                    "frame_anchor": "transition",
                    "visual": "Bridge with the observed subject motion.",
                    "motion": "Follow movement.",
                    "transition_goal": "Connect the two supplied states.",
                    "subtitle": None,
                    "voiceover": "Optional narration.",
                    "sound_effects": ["movement swish"],
                    "notes": None,
                },
                {
                    "scene_index": 3,
                    "start_second": 9,
                    "end_second": 12,
                    "frame_anchor": "last_frame",
                    "visual": "Arrive at the exact supplied ending state.",
                    "motion": "Settle into the final composition.",
                    "transition_goal": "End at last frame.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["music resolve"],
                    "notes": None,
                },
            ],
            "sound_design": {"music": "gentle build", "ambience": "room tone"},
            "rationale": "Bridge the supplied frames.",
        }
    )
    analysis = _analysis()

    storyboard = await provider.generate_frame_anchored_video_storyboard(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        analysis,
        12,
        "9:16",
    )

    assert storyboard.scenes[0].frame_anchor == "first_frame"
    assert storyboard.scenes[-1].frame_anchor == "last_frame"
    content = captured[0]["input"][1]["content"]
    assert content[1] == {"type": "input_image", "image_url": FIRST_FRAME_URL}
    assert content[3] == {"type": "input_image", "image_url": LAST_FRAME_URL}
    assert '"frame_analysis"' in content[0]["text"]
    assert "first frame facts" in content[0]["text"]
    system_prompt = captured[0]["input"][0]["content"]
    _assert_no_legacy_content(system_prompt)
    assert "first_frame" in system_prompt
    assert "last_frame" in system_prompt
    assert "Do not invent" in system_prompt
    assert "duration_seconds" in system_prompt
    assert "aspect_ratio" in system_prompt
    assert "subtitle" in system_prompt
    assert "voiceover" in system_prompt
    assert "sound_effects" in system_prompt
    assert "cinematic_beat" in system_prompt
    assert "exact beat_id" in system_prompt
    assert (
        "Do not alter or translate text that is visibly supplied by either target image"
        in system_prompt
    )


@pytest.mark.asyncio
async def test_gateway_storyboard_normalizes_string_sound_effects() -> None:
    provider, _captured = _gateway_provider_with_responses(
        {
            "duration_seconds": 12,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 6,
                    "frame_anchor": "first_frame",
                    "visual": "Open from the supplied first-frame base layer.",
                    "sound_effects": "soft cinematic rise",
                },
                {
                    "scene_index": 2,
                    "start_second": 6,
                    "end_second": 12,
                    "frame_anchor": "last_frame",
                    "visual": "Resolve at the supplied last-frame base layer.",
                    "sound_effects": "golden impact burst",
                },
            ],
        }
    )

    storyboard = await provider.generate_frame_anchored_video_storyboard(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        _analysis(),
        12,
        "9:16",
    )

    assert storyboard.scenes[0].sound_effects == ["soft cinematic rise"]
    assert storyboard.scenes[1].sound_effects == ["golden impact burst"]


@pytest.mark.asyncio
async def test_gateway_storyboard_prioritizes_target_truth_and_reference_constraints() -> None:
    provider, captured = _gateway_provider_with_responses(
        {
            "duration_seconds": 12,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 8,
                    "frame_anchor": "first_frame",
                    "visual": "Keep the supplied target subject visible through the main action.",
                    "motion": "Adapt a gradual push-in.",
                    "transition_goal": "Begin from the supplied first frame.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["light effect accent"],
                    "notes": None,
                },
                {
                    "scene_index": 2,
                    "start_second": 8,
                    "end_second": 12,
                    "frame_anchor": "last_frame",
                    "visual": "Reach the exact supplied ending state.",
                    "motion": "Settle into the final composition.",
                    "transition_goal": "End at the supplied last frame.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["music resolve"],
                    "notes": None,
                },
            ],
            "sound_design": {"music": "instrumental", "ambience": "room tone"},
            "rationale": "Target truth takes priority.",
        }
    )
    analysis = FrameAnalysis.model_validate(
        {**_analysis().model_dump(), "reference_video_analysis": _reference_video_analysis_data()}
    )

    await provider.generate_frame_anchored_video_storyboard(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        analysis,
        12,
        "9:16",
    )

    system_prompt = captured[0]["input"][0]["content"].lower()
    assert "last-frame base" in system_prompt
    assert "behavior_graph" in system_prompt
    assert "timeline_adaptation_plan" in system_prompt
    assert "reference seconds" in system_prompt
    assert "causal behavior" in system_prompt
    assert "camera" in system_prompt
    assert "transitions" in system_prompt
    assert "effects" in system_prompt
    assert "advertising objective" in system_prompt
    assert "visual_identity_mappings" in system_prompt
    assert "replace_with_target" in system_prompt
    assert "morph_to_target" in system_prompt
    assert "preserve_through_last_anchor" in system_prompt
    assert "required final overlay" in system_prompt
    assert "diamond" not in system_prompt


def _gateway_provider_with_responses(
    response_data: dict[str, object],
) -> tuple[GatewayResponsesLLMProvider, list[dict[str, object]]]:
    captured: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"output_text": json.dumps(response_data)})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://model.example.test/v1",
    )
    provider = GatewayResponsesLLMProvider(
        api_key="gateway-key",
        base_url="https://model.example.test/v1",
        model="gpt-5.5",
        http_client=client,
    )
    return provider, captured


def _visual_facts(state: str) -> dict[str, object]:
    return {
        "visible_subjects": ["subject"],
        "visible_text": ["Visible text"],
        "environment": "environment",
        "composition": "composition",
        "camera_perspective": "eye level",
        "visual_style": "natural",
        "color_and_lighting": "soft daylight",
        "opening_state": state,
        "ending_state": state,
    }


def _analysis() -> FrameAnalysis:
    return FrameAnalysis(
        first_frame=FrameVisualFacts(**_visual_facts("first frame facts")),
        last_frame=FrameVisualFacts(**_visual_facts("last frame facts")),
        transition_brief=FrameTransitionBrief(
            shared_visual_facts=["shared subject"],
            continuity_requirements=["retain visible image text"],
            visual_transition="camera follows subject motion",
            narrative_arc="first frame facts to last frame facts",
        ),
        language_analysis=FrameLanguageAnalysis(
            first_frame_visible_languages=["en"],
            last_frame_visible_languages=["en"],
            recommended_output_language="en",
            reason="Visible text is English.",
        ),
    )


def _reference_video_analysis_data() -> dict[str, object]:
    return {
        "duration_seconds": 5.8,
        "sample_interval_seconds": 2,
        "segments": [
            {
                "start_second": 0,
                "end_second": 2,
                "subject_presence": {
                    "state": "continuous",
                    "visibility": "mostly_full_body",
                    "screen_position": "center",
                    "movement": "moves_forward",
                    "appearance": (
                        "The subject enters from the right edge behind foreground light."
                    ),
                    "action": (
                        "The subject walks to center, turns toward the camera, and raises "
                        "the target object."
                    ),
                    "interaction": "The gesture intensifies the surrounding gold particles.",
                },
                "camera": {"movement": "slow_push_in", "intensity": "medium"},
                "transition": {
                    "type": "continuous_motion",
                    "description": "Movement continues into the next interval.",
                },
                "effects": ["gold particles"],
                "confidence": "high",
            }
        ],
        "adapted_constraints": {
            "subject_presence": {
                "strength": "preferred",
                "instruction": (
                    "Use reference subject staging through middle scenes, then satisfy the exact "
                    "target endpoint anchors."
                ),
            },
            "camera_pattern": {
                "strength": "preferred",
                "instruction": "Use a gradual push-in where compatible.",
            },
            "transition_pattern": {
                "strength": "preferred",
                "instruction": "Prefer continuous movement transitions.",
            },
            "effects_pattern": {
                "strength": "preferred",
                "instruction": (
                    "Preserve reference visible effects through the middle unless a mapping "
                    "replaces them."
                ),
            },
        },
    }


def _assert_no_legacy_content(system_prompt: str) -> None:
    lowered = system_prompt.lower()
    for forbidden in (
        "meta/facebook",
        "creative_strategy",
        "product_name",
        "boss",
        "vip",
        "vfx",
        "3a",
    ):
        assert forbidden not in lowered


@pytest.mark.asyncio
async def test_gateway_director_plan_normalizes_semantically_valid_object_shapes() -> None:
    """Accept the alternate object-shaped director plan observed in the deployed worker log."""
    provider, _captured = _gateway_provider_with_responses(
        {
            "narrative_objective": "Build from the supplied opening anchor to a decisive ending.",
            "attention_path": [
                {
                    "time_ratio": 0.0,
                    "focus": "opening subject and composition",
                    "method": "establish the available target identity",
                },
                {
                    "time_ratio": 0.62,
                    "focus": "visible consequence of the central action",
                    "method": "tighten the in-shot viewpoint and peak the effect",
                },
            ],
            "tension_curve": [
                {"phase": "setup", "start_ratio": 0.0, "end_ratio": 0.18},
                {"phase": "trigger", "start_ratio": 0.18, "end_ratio": 0.36},
                {"phase": "escalation", "start_ratio": 0.36, "end_ratio": 0.62},
                {"phase": "climax", "start_ratio": 0.62, "end_ratio": 0.82},
                {"phase": "resolution", "start_ratio": 0.82, "end_ratio": 1.0},
            ],
            "climax_beats": [
                {
                    "beat_id": "visible_impact",
                    "start_ratio": 0.62,
                    "end_ratio": 0.82,
                    "source_evidence": {
                        "observed_action": "The reference analysis records a causal action.",
                        "observed_result": "The action produces a visible result.",
                    },
                    "attention_objective": "Hold attention on the visible result.",
                    "camera_instruction": "Use an in-shot push-in at the impact.",
                    "action_requirement": "Show action before its visible consequence.",
                    "effect_requirement": "Peak the available effect at the consequence.",
                    "importance": "primary",
                    "dependencies": [],
                }
            ],
            "overlay_lifecycle_plan": [
                {
                    "element": "observed interface layer",
                    "observed_in": "reference ending",
                    "observed_final_requirement": "remain readable at the ending",
                    "decision": "persist through target ending",
                    "lifecycle": "appear after impact and remain through the ending",
                    "constraints": ["keep it readable without hiding target content"],
                }
            ],
            "anchor_adaptation_plan": {
                "opening_anchor": {"instruction": "Start from the supplied first frame."},
                "transition_strategy": {"instruction": "Carry causal progression in-shot."},
                "ending_anchor": {"instruction": "Resolve to the supplied last frame."},
            },
            "anti_flattening_constraints": [
                {
                    "constraint": "Keep cause, action, impact, and result distinct.",
                    "application": "Peak the effect only at impact.",
                }
            ],
        }
    )

    plan = await provider.direct_frame_anchored_video_storyboard(
        FIRST_FRAME_URL,
        LAST_FRAME_URL,
        _analysis(),
        12,
        "9:16",
    )

    assert plan.attention_path[0].startswith("At 0%")
    assert plan.tension_curve == ["setup", "trigger", "escalation", "climax", "resolution"]
    assert plan.climax_beats[0].stage == "climax"
    assert plan.climax_beats[0].importance == "core"
    assert plan.climax_beats[0].source_evidence == [
        "observed_action: The reference analysis records a causal action.",
        "observed_result: The action produces a visible result.",
    ]
    assert plan.overlay_lifecycle_plan[0].strategy == "persist_to_final"
    assert "opening_anchor" in plan.anchor_adaptation_plan[0]
    assert plan.anti_flattening_constraints == [
        "Keep cause, action, impact, and result distinct. "
        "Application: Peak the effect only at impact."
    ]
