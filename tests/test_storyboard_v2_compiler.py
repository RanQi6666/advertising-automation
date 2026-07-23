import logging

import pytest

from backend.app.schemas.ai import (
    FrameAnalysis,
    FrameAnchoredDirectorPlan,
    FrameAnchoredStoryboardDraft,
)
from backend.app.services.storyboard_v2_compiler import (
    _compile_scene_boundaries,
    compile_storyboard_v2,
)


def _analysis() -> FrameAnalysis:
    return FrameAnalysis.model_validate(
        {
            "first_frame": {
                "visible_subjects": ["target hero"],
                "visible_text": ["START"],
                "environment": "target environment",
                "composition": "opening composition",
                "camera_perspective": "eye level",
                "visual_style": "AAA game cinematic",
                "color_and_lighting": "high contrast",
                "opening_state": "hero ready",
                "ending_state": "",
            },
            "last_frame": {
                "visible_subjects": ["target hero"],
                "visible_text": ["REWARD"],
                "environment": "target environment",
                "composition": "ending composition",
                "camera_perspective": "eye level",
                "visual_style": "AAA game cinematic",
                "color_and_lighting": "high contrast",
                "opening_state": "",
                "ending_state": "reward visible",
            },
            "transition_brief": {
                "shared_visual_facts": ["target hero"],
                "continuity_requirements": ["keep target identity"],
                "visual_transition": "continuous action",
                "narrative_arc": "prepare, strike, payoff, return",
            },
            "language_analysis": {
                "first_frame_visible_languages": ["en"],
                "last_frame_visible_languages": ["en"],
                "recommended_output_language": "en",
                "reason": "Target text is English.",
            },
            "reference_video_analysis": {
                "duration_seconds": 4.0,
                "sample_interval_seconds": 1.0,
                "segments": [
                    {
                        "start_second": 0.0,
                        "end_second": 4.0,
                        "subject_presence": {
                            "state": "active",
                            "visibility": "visible",
                            "screen_position": "center",
                            "movement": "prepare and strike",
                            "appearance": "reference actor",
                            "action": "causal strike",
                            "interaction": "contacts object",
                        },
                        "camera": {"movement": "push in", "intensity": "high"},
                        "transition": {"type": "continuous", "description": "one shot"},
                        "effects": ["impact sparks"],
                        "confidence": "high",
                    }
                ],
                "behavior_graph": {
                    "entities": ["actor", "object"],
                    "beats": [
                        {
                            "beat_id": "source_action",
                            "reference_start_second": 1.0,
                            "reference_end_second": 3.0,
                            "description": "Actor performs the core action.",
                            "visible_evidence": ["action", "visible result"],
                            "behavior_type": "action",
                            "importance": "core",
                            "minimum_readable_duration_seconds": 0.5,
                            "depends_on": [],
                        }
                    ],
                },
                "visual_identity_mappings": [],
                "adapted_constraints": {
                    "subject_presence": {
                        "strength": "preferred",
                        "instruction": "Adapt subject behavior to the target identity.",
                    },
                    "camera_pattern": {
                        "strength": "preferred",
                        "instruction": "Adapt the push-in camera pattern.",
                    },
                    "transition_pattern": {
                        "strength": "preferred",
                        "instruction": "Keep continuous motion where feasible.",
                    },
                    "effects_pattern": {
                        "strength": "preferred",
                        "instruction": "Adapt impact effects to the target world.",
                    },
                },
            },
            "director_plan": _director_plan().model_dump(mode="json"),
        }
    )


def _director_plan() -> FrameAnchoredDirectorPlan:
    return FrameAnchoredDirectorPlan.model_validate(
        {
            "narrative_objective": "Execute a readable causal action and return.",
            "attention_path": ["setup", "action", "payoff", "ending"],
            "tension_curve": ["setup", "escalation", "climax", "resolution"],
            "climax_beats": [
                {
                    "beat_id": "impact_peak",
                    "stage": "climax",
                    "source_evidence": ["The source action has a visible impact."],
                    "start_ratio": 0.3,
                    "end_ratio": 0.7,
                    "attention_objective": "Read the impact.",
                    "camera_instruction": "Push toward impact.",
                    "action_requirement": "Show the action before the result.",
                    "effect_requirement": "Peak effects after contact.",
                    "importance": "core",
                    "depends_on": [],
                }
            ],
            "overlay_lifecycle_plan": [],
            "action_arc_windows": [
                {
                    "window_id": "prepare",
                    "phase": "preparation",
                    "start_ratio": 0.0,
                    "end_ratio": 0.25,
                    "objective": "Prepare.",
                    "subject_motion_intensity": 0.3,
                    "camera_intensity": 0.2,
                    "effect_intensity": 0.1,
                    "depends_on": [],
                },
                {
                    "window_id": "act",
                    "phase": "action",
                    "start_ratio": 0.25,
                    "end_ratio": 0.7,
                    "objective": "Act.",
                    "subject_motion_intensity": 0.9,
                    "camera_intensity": 0.7,
                    "effect_intensity": 0.8,
                    "depends_on": ["prepare"],
                },
                {
                    "window_id": "lock",
                    "phase": "final_lock",
                    "start_ratio": 0.7,
                    "end_ratio": 1.0,
                    "objective": "Return and lock.",
                    "subject_motion_intensity": 0.1,
                    "camera_intensity": 0.1,
                    "effect_intensity": 0.0,
                    "depends_on": ["act"],
                },
            ],
            "signature_moment_plan": [
                {
                    "moment_id": "signature_action",
                    "moment_type": "combined",
                    "source_evidence": ["The source action has a visible impact."],
                    "source_behavior_beat_ids": ["source_action"],
                    "transfer_role": "primary_action",
                    "strategy": "adapt",
                    "target_adaptation": "Use the target hero and object.",
                    "adapted_action": "The target hero performs the causal strike.",
                    "temporary_divergence": "Allow a dynamic middle pose.",
                    "camera_support": "Push and reframe during the strike.",
                    "effect_support": "Peak sparks after contact.",
                    "visible_payoff": "Show the changed object and reward.",
                    "return_strategy": "Settle into the exact ending composition.",
                    "assigned_beat_id": "impact_peak",
                }
            ],
            "final_anchor_return": "Settle into the exact ending composition.",
            "anchor_adaptation_plan": ["Use supplied endpoint images."],
            "anti_flattening_constraints": ["Keep action and payoff readable."],
        }
    )


def _draft() -> FrameAnchoredStoryboardDraft:
    return FrameAnchoredStoryboardDraft.model_validate(
        {
            "duration_seconds": 99,
            "aspect_ratio": "1:1",
            "scenes": [
                {
                    "scene_index": 8,
                    "start_second": 0.4,
                    "end_second": 2.0,
                    "frame_anchor": "opening",
                    "visual": "Hold the supplied opening identity and prepare the strike.",
                    "motion": "The hero coils into a readable preparation.",
                    "transition_goal": "Depart from the opening pose without a cut.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["rising metal tone"],
                    "notes": None,
                    "cinematic_beat": None,
                    "cinematic_beats": [],
                    "signature_moment_ids": ["signature_action"],
                    "source_behavior_beat_ids": ["source_action"],
                    "phase_tags": ["preparation"],
                    "execution_actions": [],
                    "camera_instruction": "Begin a controlled push.",
                    "tension_stage_hint": "setup",
                    "action_result_requirement": None,
                    "effect_timing": None,
                    "subject_motion_intensity": 0.3,
                    "camera_intensity": 0.2,
                    "effect_intensity": 0.1,
                    "anchor_return_instruction": None,
                    "overlay_instruction": None,
                    "anti_flattening_requirement": "Do not skip preparation.",
                },
                {
                    "scene_index": 3,
                    "start_second": 2.0,
                    "end_second": 8.0,
                    "frame_anchor": "action_window",
                    "visual": "The target hero performs the causal strike and reveals the result.",
                    "motion": "A readable wind-up, strike, contact, and recoil happen in sequence.",
                    "transition_goal": "Keep the action continuous.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["impact accent"],
                    "notes": None,
                    "cinematic_beat": "impact_peak",
                    "cinematic_beats": ["impact_peak", "unknown_beat"],
                    "signature_moment_ids": ["signature_action", "unknown_moment"],
                    "source_behavior_beat_ids": ["source_action", "unknown_source"],
                    "phase_tags": ["action", "payoff"],
                    "execution_actions": [
                        {
                            "executor_kind": "target_subject",
                            "assertion": "affirmed",
                            "action_or_state_change": (
                                "The target hero completes the causal strike."
                            ),
                            "signature_moment_ids": ["signature_action"],
                            "source_behavior_beat_ids": ["source_action"],
                        },
                        {
                            "executor_kind": "target_subject",
                            "assertion": "affirmed",
                            "action_or_state_change": (
                                "The target hero completes the causal strike."
                            ),
                            "signature_moment_ids": ["signature_action"],
                            "source_behavior_beat_ids": ["source_action"],
                        },
                        {
                            "executor_kind": "target_subject",
                            "assertion": "affirmed",
                            "action_or_state_change": "A distinct recoil settles the hero.",
                            "signature_moment_ids": ["signature_action"],
                            "source_behavior_beat_ids": ["source_action"],
                        },
                    ],
                    "camera_instruction": "Rapid push to impact, then recover framing.",
                    "tension_stage_hint": "final_lock",
                    "action_result_requirement": (
                        "The changed object and reward must read after impact."
                    ),
                    "effect_timing": "Peak sparks only after contact.",
                    "subject_motion_intensity": 0.95,
                    "camera_intensity": 0.8,
                    "effect_intensity": 0.85,
                    "anchor_return_instruction": "Begin returning to the final composition.",
                    "overlay_instruction": None,
                    "anti_flattening_requirement": (
                        "Keep wind-up, contact, result, and recoil distinct."
                    ),
                },
                {
                    "scene_index": 1,
                    "start_second": 8.0,
                    "end_second": 11.5,
                    "frame_anchor": "final_lock",
                    "visual": "Settle into and hold the exact supplied final frame.",
                    "motion": "Motion stabilizes into the ending state.",
                    "transition_goal": "Return continuously to the final anchor.",
                    "subtitle": None,
                    "voiceover": None,
                    "sound_effects": ["resolved ambience"],
                    "notes": None,
                    "cinematic_beat": None,
                    "cinematic_beats": [],
                    "signature_moment_ids": [],
                    "source_behavior_beat_ids": ["source_action"],
                    "phase_tags": ["final_hold"],
                    "execution_actions": [],
                    "camera_instruction": "Lock camera at the end.",
                    "tension_stage_hint": "final_lock",
                    "action_result_requirement": None,
                    "effect_timing": None,
                    "subject_motion_intensity": 0.05,
                    "camera_intensity": 0.05,
                    "effect_intensity": 0.0,
                    "anchor_return_instruction": "Match the exact final composition.",
                    "overlay_instruction": None,
                    "anti_flattening_requirement": "Preserve a readable final hold.",
                },
            ],
            "sound_design": {"music": "rise, impact, resolve", "ambience": "target ambience"},
            "rationale": "One continuous AAA-style action arc.",
        }
    )


def test_compiler_generates_stable_claims_and_authoritative_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING):
        storyboard = compile_storyboard_v2(
            _draft(),
            _analysis(),
            duration_seconds=12,
            aspect_ratio="9:16",
        )

    assert storyboard.duration_seconds == 12
    assert storyboard.aspect_ratio == "9:16"
    assert [scene.scene_index for scene in storyboard.scenes] == [1, 2, 3]
    assert [scene.frame_anchor for scene in storyboard.scenes] == [
        "first_frame",
        "transition",
        "last_frame",
    ]
    assert storyboard.scenes[0].start_second == 0
    assert storyboard.scenes[-1].end_second == 12
    assert storyboard.scenes[1].tension_stage == "climax"
    assert storyboard.scenes[-1].tension_stage == "resolution"

    action_scene = storyboard.scenes[1]
    assert action_scene.signature_moment_ids == ["signature_action"]
    assert action_scene.source_behavior_beat_ids == ["source_action"]
    assert action_scene.cinematic_beats == ["impact_peak"]
    claim_ids = [item.claim_id for item in action_scene.execution_evidence]
    assert claim_ids == [
        "__sbv2_claim_001__",
        "__sbv2_claim_001__",
        "__sbv2_claim_002__",
    ]
    assert {item.phase for item in action_scene.phase_evidence} == {"action", "payoff"}
    assert "unknown_moment" in caplog.text
    assert "unknown_source" in caplog.text
    assert "unknown_beat" in caplog.text


def test_compiler_does_not_create_phase_evidence_without_both_id_sides() -> None:
    storyboard = compile_storyboard_v2(
        _draft(),
        _analysis(),
        duration_seconds=12,
        aspect_ratio="9:16",
    )

    final_scene = storyboard.scenes[-1]
    assert final_scene.signature_moment_ids == []
    assert final_scene.phase_evidence == []


def test_compiler_preserves_creative_text_verbatim() -> None:
    draft = _draft()
    storyboard = compile_storyboard_v2(
        draft,
        _analysis(),
        duration_seconds=12,
        aspect_ratio="9:16",
    )

    for source, compiled in zip(draft.scenes, storyboard.scenes, strict=True):
        assert compiled.visual == source.visual
        assert compiled.motion == source.motion
        assert compiled.camera_instruction == source.camera_instruction
        assert compiled.effect_timing == source.effect_timing

@pytest.mark.parametrize(
    ("duration_seconds", "scene_count", "time_hints"),
    [
        (4.0, 5, [(None, None)] * 5),
        (12.0, 5, [(11.99, 11.99)] * 5),
        (12.0, 5, [(9.0, 10.0), (7.0, 8.0), (5.0, 6.0), (3.0, 4.0), (1.0, 2.0)]),
        (30.0, 8, [(-5.0, 90.0)] * 8),
        (12.0, 20, [(11.999, 11.999)] * 20),
    ],
)
def test_scene_boundaries_are_contiguous_and_reserve_dynamic_room(
    duration_seconds: float,
    scene_count: int,
    time_hints: list[tuple[float | None, float | None]],
) -> None:
    template = _draft().scenes[0]
    scenes = [
        template.model_copy(
            update={
                "scene_index": index + 1,
                "start_second": start_second,
                "end_second": end_second,
            }
        )
        for index, (start_second, end_second) in enumerate(time_hints)
    ]

    boundaries = _compile_scene_boundaries(
        scenes,
        duration_seconds=duration_seconds,
    )

    assert len(boundaries) == scene_count
    assert boundaries[0][0] == 0.0
    assert boundaries[-1][1] == duration_seconds
    assert all(
        left[1] == right[0]
        for left, right in zip(boundaries, boundaries[1:], strict=False)
    )
    minimum_scene_duration = duration_seconds / scene_count / 100
    assert all(
        end_second - start_second >= minimum_scene_duration - 1e-9
        for start_second, end_second in boundaries
    )


def test_two_scene_storyboard_keeps_climax_when_first_scene_overlaps_director_beat() -> None:
    draft = _draft().model_copy(
        update={
            "duration_seconds": 1,
            "scenes": [
                _draft().scenes[0].model_copy(
                    update={"start_second": 0.0, "end_second": 0.5}
                ),
                _draft().scenes[1].model_copy(
                    update={"start_second": 0.5, "end_second": 1.0}
                ),
            ],
        }
    )

    storyboard = compile_storyboard_v2(
        draft,
        _analysis(),
        duration_seconds=1,
        aspect_ratio="9:16",
    )

    assert storyboard.scenes[0].frame_anchor == "first_frame"
    assert storyboard.scenes[0].tension_stage == "climax"
    assert storyboard.scenes[-1].tension_stage == "resolution"

