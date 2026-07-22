import pytest

from backend.app.schemas.ai import (
    DirectorActionCoverageReview,
    FrameAnalysis,
    FrameAnchoredDirectorPlan,
    FrameAnchoredStoryboard,
    validate_director_coverage,
)
from backend.app.services.storyboard_director_coverage_service import (
    review_director_action_coverage,
    validate_final_storyboard_action_coverage,
)


def _analysis(
    *,
    include_reference: bool = True,
    behavior_type: str = "action",
) -> FrameAnalysis:
    payload: dict[str, object] = {
        "first_frame": {
            "visible_subjects": ["target subject"],
            "visible_text": [],
            "environment": "target environment",
            "composition": "opening composition",
            "camera_perspective": "eye level",
            "visual_style": "cinematic",
            "color_and_lighting": "controlled contrast",
            "opening_state": "stable opening anchor",
        },
        "last_frame": {
            "visible_subjects": ["target subject"],
            "visible_text": [],
            "environment": "target environment",
            "composition": "ending composition",
            "camera_perspective": "eye level",
            "visual_style": "cinematic",
            "color_and_lighting": "controlled contrast",
            "ending_state": "stable ending anchor",
        },
        "transition_brief": {
            "shared_visual_facts": ["same target subject"],
            "continuity_requirements": ["begin and end on supplied anchors"],
            "visual_transition": "diverge for action, then return continuously",
            "narrative_arc": "setup, action, payoff, return, lock",
        },
        "language_analysis": {
            "first_frame_visible_languages": [],
            "last_frame_visible_languages": [],
            "recommended_output_language": "en",
            "reason": "No visible text requires preservation.",
        },
    }
    if include_reference:
        payload["reference_video_analysis"] = {
            "duration_seconds": 6,
            "sample_interval_seconds": 2,
            "segments": [
                {
                    "start_second": 0,
                    "end_second": 2,
                    "subject_presence": {
                        "state": "continuous",
                        "visibility": "mostly_full_body",
                        "screen_position": "center",
                        "movement": "advances into the action",
                        "appearance": "same reference subject remains visible",
                        "action": "subject initiates a causal change",
                        "interaction": "the environment visibly responds",
                    },
                    "camera": {"movement": "guided follow", "intensity": "medium"},
                    "transition": {"type": "continuous", "description": "no edit"},
                    "effects": ["supportive buildup"],
                    "confidence": "high",
                },
                {
                    "start_second": 2,
                    "end_second": 6,
                    "subject_presence": {
                        "state": "continuous",
                        "visibility": "mostly_full_body",
                        "screen_position": "center",
                        "movement": "finishes the state-changing action",
                        "appearance": "same reference subject remains visible",
                        "action": "subject completes the state-changing action",
                        "interaction": "visible consequence",
                    },
                    "camera": {"movement": "continuous emphasis", "intensity": "high"},
                    "transition": {"type": "continuous", "description": "no edit"},
                    "effects": ["supportive effect peak"],
                    "confidence": "high",
                },
            ],
            "adapted_constraints": {
                "subject_presence": {
                    "strength": "preferred",
                    "instruction": "transfer behavior only",
                },
                "camera_pattern": {"strength": "preferred", "instruction": "adapt camera emphasis"},
                "transition_pattern": {"strength": "preferred", "instruction": "keep one shot"},
                "effects_pattern": {"strength": "preferred", "instruction": "support action"},
            },
            "behavior_graph": {
                "entities": ["reference subject"],
                "beats": [
                    {
                        "beat_id": "core_behavior",
                        "reference_start_second": 1,
                        "reference_end_second": 4,
                        "description": "A reliable causal action changes the visible state.",
                        "visible_evidence": ["subject motion precedes a visible payoff"],
                        "behavior_type": behavior_type,
                        "importance": "core",
                        "minimum_readable_duration_seconds": 0.8,
                        "depends_on": [],
                    }
                ],
            },
        }
    return FrameAnalysis.model_validate(payload)


def _analysis_with_core_beats(
    *,
    behavior_types: list[str],
    minimum_readable_durations: list[float] | None = None,
) -> FrameAnalysis:
    analysis = _analysis(behavior_type=behavior_types[0])
    reference = analysis.reference_video_analysis
    assert reference is not None
    graph = reference.behavior_graph
    assert graph is not None
    durations = minimum_readable_durations or [0.8] * len(behavior_types)
    beats = [
        graph.beats[0].model_copy(
            update={
                "beat_id": f"core_behavior_{index}",
                "behavior_type": behavior_type,
                "minimum_readable_duration_seconds": durations[index - 1],
                "description": f"Core {behavior_type} behavior {index} changes visible state.",
            }
        )
        for index, behavior_type in enumerate(behavior_types, start=1)
    ]
    return analysis.model_copy(
        update={
            "reference_video_analysis": reference.model_copy(
                update={"behavior_graph": graph.model_copy(update={"beats": beats})}
            )
        }
    )


def _plan(*, source_ids: list[str]) -> FrameAnchoredDirectorPlan:
    return FrameAnchoredDirectorPlan.model_validate(
        {
            "narrative_objective": "Execute a target-compatible causal action and return.",
            "attention_path": ["anchor", "action", "payoff", "return", "final lock"],
            "tension_curve": ["setup", "climax", "resolution"],
            "climax_beats": [
                {
                    "beat_id": "core_peak",
                    "stage": "climax",
                    "source_evidence": ["The reference contains a reliable causal peak."],
                    "start_ratio": 0.2,
                    "end_ratio": 0.7,
                    "attention_objective": "Keep the causal action readable.",
                    "camera_instruction": "Support without replacing action.",
                    "action_requirement": "Execute subject/state motion.",
                    "effect_requirement": "Reveal the visible payoff.",
                    "importance": "core",
                }
            ],
            "signature_moment_plan": [
                {
                    "moment_id": "signature_action",
                    "moment_type": "combined",
                    "source_evidence": ["Action, emphasis, and consequence are jointly visible."],
                    "source_behavior_beat_ids": source_ids,
                    "transfer_role": "primary_action",
                    "strategy": "adapt",
                    "target_adaptation": "Adapt the causal role to target facts.",
                    "adapted_action": "Execute target-compatible subject/state motion.",
                    "temporary_divergence": "Allow a distinct middle pose and composition.",
                    "camera_support": "Reframe continuously around execution.",
                    "effect_support": "Support the consequence without replacing action.",
                    "visible_payoff": "Show the resulting target-state change.",
                    "return_strategy": "Settle continuously into the exact last anchor.",
                    "assigned_beat_id": "core_peak",
                }
            ],
            "action_arc_windows": [
                {
                    "window_id": "preparation",
                    "phase": "preparation",
                    "start_ratio": 0.0,
                    "end_ratio": 0.15,
                    "objective": "Prepare subject/state execution from the opening anchor.",
                    "subject_motion_intensity": 0.25,
                    "camera_intensity": 0.15,
                    "effect_intensity": 0.05,
                    "depends_on": [],
                },
                {
                    "window_id": "action",
                    "phase": "action",
                    "start_ratio": 0.15,
                    "end_ratio": 0.58,
                    "objective": "Execute readable subject/state motion.",
                    "subject_motion_intensity": 0.9,
                    "camera_intensity": 0.65,
                    "effect_intensity": 0.35,
                    "depends_on": [],
                },
                {
                    "window_id": "payoff",
                    "phase": "payoff",
                    "start_ratio": 0.5,
                    "end_ratio": 0.72,
                    "objective": "Show the visible causal result.",
                    "subject_motion_intensity": 0.55,
                    "camera_intensity": 0.5,
                    "effect_intensity": 0.8,
                    "depends_on": ["action"],
                },
                {
                    "window_id": "return",
                    "phase": "return",
                    "start_ratio": 0.68,
                    "end_ratio": 0.9,
                    "objective": "Return continuously to the final anchor.",
                    "subject_motion_intensity": 0.45,
                    "camera_intensity": 0.4,
                    "effect_intensity": 0.25,
                    "depends_on": ["payoff"],
                },
                {
                    "window_id": "final_lock",
                    "phase": "final_lock",
                    "start_ratio": 0.88,
                    "end_ratio": 1,
                    "objective": "Hold the exact supplied last frame.",
                    "subject_motion_intensity": 0.05,
                    "camera_intensity": 0.05,
                    "effect_intensity": 0.05,
                    "depends_on": ["return"],
                },
            ],
            "final_anchor_return": "Return after payoff and stabilize on the exact last frame.",
            "anchor_adaptation_plan": ["Use endpoints as anchors, not middle-frame pose locks."],
            "anti_flattening_constraints": ["Do not replace action with camera or effects."],
        }
    )


def _valid_final_inputs() -> tuple[
    FrameAnchoredStoryboard,
    FrameAnalysis,
    DirectorActionCoverageReview,
]:
    plan = _plan(source_ids=["core_behavior"])
    analysis = _analysis().model_copy(update={"director_plan": plan})
    review = review_director_action_coverage(analysis, plan)
    storyboard = FrameAnchoredStoryboard.model_validate(
        {
            "duration_seconds": 10,
            "aspect_ratio": "9:16",
            "scenes": [
                {
                    "scene_index": 1,
                    "start_second": 0,
                    "end_second": 1,
                    "frame_anchor": "first_frame",
                    "visual": "Hold the exact supplied opening anchor.",
                    "motion": "Prepare the subject for the continuous causal action.",
                    "transition_goal": "Prepare and depart from the opening anchor in-shot.",
                },
                {
                    "scene_index": 2,
                    "start_second": 1,
                    "end_second": 6,
                    "frame_anchor": "transition",
                    "visual": "Execute the target-compatible causal action and payoff.",
                    "motion": "The subject completes readable state-changing motion.",
                    "cinematic_beats": ["core_peak"],
                    "camera_instruction": "Reframe continuously around the action.",
                    "action_result_requirement": "Show the visible target-state change.",
                    "effect_timing": "Peak only after the causal motion reads.",
                    "signature_moment_ids": ["signature_action"],
                    "source_behavior_beat_ids": ["core_behavior"],
                    "subject_motion_intensity": 0.9,
                    "camera_intensity": 0.65,
                    "effect_intensity": 0.8,
                },
                {
                    "scene_index": 3,
                    "start_second": 6,
                    "end_second": 8,
                    "frame_anchor": "transition",
                    "visual": "Resolve the payoff and return to the supplied ending composition.",
                    "motion": "The subject settles toward the final state.",
                    "anchor_return_instruction": (
                        "Continuously restore final pose, framing, and camera."
                    ),
                    "signature_moment_ids": ["signature_action"],
                },
                {
                    "scene_index": 4,
                    "start_second": 8,
                    "end_second": 10,
                    "frame_anchor": "last_frame",
                    "visual": "Lock the exact supplied last frame.",
                },
            ],
        }
    )
    return storyboard, analysis, review


def _valid_short_final_inputs() -> tuple[
    FrameAnchoredStoryboard,
    FrameAnalysis,
    DirectorActionCoverageReview,
]:
    storyboard, analysis, review = _valid_final_inputs()
    short_storyboard = storyboard.model_copy(
        update={
            "duration_seconds": 4,
            "scenes": [
                storyboard.scenes[0].model_copy(update={"end_second": 0.5}),
                storyboard.scenes[1].model_copy(
                    update={
                        "start_second": 0.5,
                        "end_second": 3.25,
                        "anchor_return_instruction": "Return continuously after the payoff.",
                    }
                ),
                storyboard.scenes[-1].model_copy(
                    update={"scene_index": 3, "start_second": 3.25, "end_second": 4}
                ),
            ],
        }
    )
    return short_storyboard, analysis, review


def test_review_marks_unlinked_core_behavior_for_correction() -> None:
    review = review_director_action_coverage(_analysis(), _plan(source_ids=[]))
    assert review.status == "corrective"
    assert review.uncovered_core_behavior_beat_ids == ["core_behavior"]
    assert review.correction_requirements == [
        "Execute uncovered core behavior beat core_behavior in subject/state motion "
        "and show its visible payoff."
    ]


def test_review_passes_valid_adapted_action() -> None:
    review = review_director_action_coverage(_analysis(), _plan(source_ids=["core_behavior"]))
    assert review.status == "pass"
    assert review.covered_core_behavior_beat_ids == ["core_behavior"]
    assert review.correction_requirements == []


def test_review_rejects_effect_only_flattening() -> None:
    plan = _plan(source_ids=["core_behavior"])
    plan = plan.model_copy(
        update={
            "action_arc_windows": [
                window.model_copy(
                    update={
                        "subject_motion_intensity": 0.05,
                        "effect_intensity": 0.9 if window.phase == "action" else 0.1,
                    }
                )
                for window in plan.action_arc_windows
            ]
        }
    )
    review = review_director_action_coverage(_analysis(), plan)
    assert review.status == "corrective"
    assert review.correction_requirements == [
        "Increase subject/state motion for the core action; camera or effects alone "
        "cannot execute it."
    ]


def test_review_rejects_camera_only_flattening() -> None:
    plan = _plan(source_ids=["core_behavior"])
    plan = plan.model_copy(
        update={
            "action_arc_windows": [
                window.model_copy(
                    update={
                        "subject_motion_intensity": 0.05,
                        "camera_intensity": 0.95 if window.phase == "action" else 0.1,
                        "effect_intensity": 0.01,
                    }
                )
                for window in plan.action_arc_windows
            ]
        }
    )
    review = review_director_action_coverage(_analysis(), plan)
    assert review.status == "corrective"
    assert review.correction_requirements == [
        "Increase subject/state motion for the core action; camera or effects alone "
        "cannot execute it."
    ]


def test_review_does_not_force_action_without_reference_core_behavior() -> None:
    analysis = _analysis(include_reference=False)
    plan = _plan(source_ids=[]).model_copy(
        update={"signature_moment_plan": [], "action_arc_windows": [], "final_anchor_return": ""}
    )
    assert review_director_action_coverage(analysis, plan).status == "pass"


def test_low_motion_reference_does_not_invent_core_action() -> None:
    analysis = _analysis(behavior_type="overlay")
    plan = _plan(source_ids=[]).model_copy(
        update={"signature_moment_plan": [], "action_arc_windows": [], "final_anchor_return": ""}
    )
    review = review_director_action_coverage(analysis, plan)
    assert review.status == "pass"
    assert review.correction_requirements == []


def test_final_validation_rejects_missing_signature_id() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    for scene in storyboard.scenes:
        scene.signature_moment_ids = []
    with pytest.raises(ValueError) as excinfo:
        validate_final_storyboard_action_coverage(storyboard, analysis, review)
    assert str(excinfo.value) == "storyboard is missing required signature moment: signature_action"


def test_final_validation_rejects_id_without_action_direction() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].motion = None
    storyboard.scenes[1].action_result_requirement = None
    with pytest.raises(ValueError) as excinfo:
        validate_final_storyboard_action_coverage(storyboard, analysis, review)
    assert str(excinfo.value) == "signature moment signature_action lacks subject/state execution"


def test_final_validation_rejects_missing_camera_support() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].camera_instruction = None
    with pytest.raises(ValueError) as excinfo:
        validate_final_storyboard_action_coverage(storyboard, analysis, review)
    assert str(excinfo.value) == "signature moment signature_action lacks camera support"


def test_final_validation_rejects_missing_effect_support() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].effect_timing = None
    with pytest.raises(ValueError) as excinfo:
        validate_final_storyboard_action_coverage(storyboard, analysis, review)
    assert str(excinfo.value) == "signature moment signature_action lacks effect support"


def test_final_validation_rejects_missing_visible_payoff() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].action_result_requirement = None
    with pytest.raises(ValueError) as excinfo:
        validate_final_storyboard_action_coverage(storyboard, analysis, review)
    assert str(excinfo.value) == "signature moment signature_action lacks visible payoff"


def test_final_validation_rejects_missing_required_source_behavior_beat() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].source_behavior_beat_ids = []
    plan = analysis.director_plan.model_copy(
        update={
            "signature_moment_plan": [
                analysis.director_plan.signature_moment_plan[0].model_copy(
                    update={"source_behavior_beat_ids": []}
                )
            ]
        }
    )
    analysis = analysis.model_copy(update={"director_plan": plan})
    with pytest.raises(ValueError) as excinfo:
        validate_final_storyboard_action_coverage(storyboard, analysis, review)
    assert str(excinfo.value) == (
        "storyboard is missing executed source behavior beats: core_behavior"
    )


def test_final_validation_rejects_missing_return() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[2].anchor_return_instruction = None
    with pytest.raises(ValueError) as excinfo:
        validate_final_storyboard_action_coverage(storyboard, analysis, review)
    assert str(excinfo.value) == "signature moment signature_action lacks linked anchor return"


def test_final_validation_rejects_unlinked_return_instruction() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[2].signature_moment_ids = []

    with pytest.raises(ValueError, match="lacks linked anchor return"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_rejects_wrong_final_end_time() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[-1].end_second = 9.5
    with pytest.raises(ValueError) as excinfo:
        validate_final_storyboard_action_coverage(storyboard, analysis, review)
    assert str(excinfo.value) == "final scene must end at requested duration"


def test_final_validation_rejects_scene_timing_out_of_order() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[2].start_second = 8
    storyboard.scenes[2].end_second = 7
    with pytest.raises(ValueError) as excinfo:
        validate_final_storyboard_action_coverage(storyboard, analysis, review)
    assert str(excinfo.value) == (
        "storyboard scene timing must be ordered within requested duration"
    )


def test_final_validation_accepts_valid_storyboard() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_short_duration_may_share_action_payoff_and_return_scene() -> None:
    storyboard, analysis, review = _valid_short_final_inputs()
    validate_director_coverage(storyboard, analysis.director_plan)
    validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_accepts_short_storyboard_with_continuous_return() -> None:
    storyboard, analysis, review = _valid_short_final_inputs()
    validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_rejects_static_source_id_with_payoff_only() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].motion = None
    storyboard.scenes[1].source_behavior_beat_ids = []
    storyboard.scenes[-1].source_behavior_beat_ids = ["core_behavior"]

    with pytest.raises(ValueError, match="lacks subject/state execution"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_rejects_primary_action_with_result_but_no_motion() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].motion = None
    assert storyboard.scenes[1].action_result_requirement

    with pytest.raises(ValueError, match="lacks subject/state execution"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_rejects_camera_effect_only_motion_as_execution() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].motion = "Camera pushes in while particles and light intensify."

    with pytest.raises(ValueError, match="lacks subject/state execution"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_state_only_core_evidence_cannot_be_replaced_by_camera_or_effects() -> None:
    analysis = _analysis(behavior_type="state")
    plan = _plan(source_ids=["core_behavior"])
    plan = plan.model_copy(
        update={
            "action_arc_windows": [
                window.model_copy(
                    update={
                        "subject_motion_intensity": 0,
                        "camera_intensity": 1,
                        "effect_intensity": 1,
                    }
                )
                if window.phase in {"action", "payoff"}
                else window
                for window in plan.action_arc_windows
            ]
        }
    )
    review = review_director_action_coverage(analysis, plan)
    assert review.status == "corrective"
    assert any("camera or effects alone" in item for item in review.correction_requirements)

    storyboard, _, _ = _valid_final_inputs()
    storyboard.scenes[1].motion = None
    storyboard.scenes[1].camera_instruction = "Camera supplies all visible activity."
    storyboard.scenes[1].effect_timing = "Effects supply all visible activity."
    analysis = analysis.model_copy(update={"director_plan": plan})
    with pytest.raises(ValueError, match="lacks subject/state execution"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_rejects_source_id_only_in_unlinked_final_lock_scene() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[1].source_behavior_beat_ids = []
    storyboard.scenes[-1].source_behavior_beat_ids = ["core_behavior"]

    with pytest.raises(ValueError, match="lacks subject/state execution"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_valid_double_infeasibility_omit_passes_without_scene_source_id() -> None:
    analysis = _analysis()
    base_plan = _plan(source_ids=["core_behavior"])
    omitted = base_plan.signature_moment_plan[0].model_copy(
        update={
            "strategy": "omit",
            "adapted_action": "",
            "temporary_divergence": "",
            "camera_support": "",
            "effect_support": "",
            "visible_payoff": "",
            "return_strategy": "",
            "assigned_beat_id": None,
            "omission_reason": (
                "Literal and adapted execution are infeasible because the target has no "
                "controllable subject or state capable of the observed causal behavior."
            ),
            "equivalent_replacement_failure": (
                "No target-compatible equivalent can preserve the causal role because the "
                "available target evidence contains no controllable replacement state."
            ),
        }
    )
    plan = base_plan.model_copy(
        update={
            "signature_moment_plan": [omitted],
            "action_arc_windows": [],
            "final_anchor_return": "",
        }
    )
    analysis = analysis.model_copy(update={"director_plan": plan})
    review = review_director_action_coverage(analysis, plan)
    assert review.status == "pass"
    assert review.validly_omitted_core_behavior_beat_ids == ["core_behavior"]

    storyboard, _, _ = _valid_final_inputs()
    storyboard = storyboard.model_copy(
        update={
            "scenes": [
                scene.model_copy(
                    update={
                        "signature_moment_ids": [],
                        "source_behavior_beat_ids": [],
                        "cinematic_beats": [],
                        "cinematic_beat": None,
                    }
                )
                for scene in storyboard.scenes
            ]
        }
    )
    validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_endpoint_mismatch_only_omit_remains_corrective() -> None:
    analysis = _analysis()
    base_plan = _plan(source_ids=["core_behavior"])
    omitted = base_plan.signature_moment_plan[0].model_copy(
        update={
            "strategy": "omit",
            "adapted_action": "",
            "temporary_divergence": "",
            "camera_support": "",
            "effect_support": "",
            "visible_payoff": "",
            "return_strategy": "",
            "assigned_beat_id": None,
            "omission_reason": "The final frame pose does not match the reference pose.",
            "equivalent_replacement_failure": (
                "The ending composition and orientation do not match the reference."
            ),
        }
    )
    plan = base_plan.model_copy(update={"signature_moment_plan": [omitted]})

    review = review_director_action_coverage(analysis, plan)

    assert review.status == "corrective"
    assert review.invalid_omission_moment_ids == ["signature_action"]


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda scenes: setattr(scenes[0], "start_second", 0.1), "first scene must start at 0"),
        (lambda scenes: setattr(scenes[1], "start_second", 1.01), "storyboard timeline has a gap"),
        (
            lambda scenes: setattr(scenes[1], "start_second", 0.99),
            "storyboard timeline has an overlap",
        ),
    ],
)
def test_final_validation_rejects_non_continuous_timeline(mutation, message: str) -> None:
    storyboard, analysis, review = _valid_final_inputs()
    mutation(storyboard.scenes)

    with pytest.raises(ValueError, match=message):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_accepts_small_float_rounding_at_scene_boundary() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[0].end_second = 1.0000001
    storyboard.scenes[1].start_second = 1.0000002

    validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_final_validation_rejects_missing_preparation() -> None:
    storyboard, analysis, review = _valid_final_inputs()
    storyboard.scenes[0].motion = None
    storyboard.scenes[0].transition_goal = None
    storyboard.scenes[0].visual = "Hold the exact supplied opening anchor."

    with pytest.raises(ValueError, match="missing preparation evidence"):
        validate_final_storyboard_action_coverage(storyboard, analysis, review)


def test_one_short_scene_may_share_all_required_action_phases() -> None:
    storyboard, analysis, review = _valid_short_final_inputs()
    shared = storyboard.scenes[1]
    shared.visual = (
        "Prepare, execute the causal state change, show the payoff, and return toward the end."
    )
    shared.transition_goal = "Preparation, execution, payoff, and return remain readable in-shot."
    shared.anchor_return_instruction = "Return continuously after payoff."
    storyboard.scenes[-1].visual = "Hold and lock the exact supplied last frame."

    validate_final_storyboard_action_coverage(storyboard, analysis, review)
