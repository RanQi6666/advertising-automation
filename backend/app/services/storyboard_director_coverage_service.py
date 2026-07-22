from __future__ import annotations

from backend.app.schemas.ai import (
    DirectorActionCoverageReview,
    FrameAnalysis,
    FrameAnchoredDirectorPlan,
    FrameAnchoredStoryboard,
    FrameAnchoredStoryboardScene,
    ReferenceBehaviorBeat,
)

_TIMELINE_TOLERANCE_SECONDS = 1e-6
_PREPARATION_TERMS = (
    "prepare",
    "preparation",
    "setup",
    "ready",
    "initiate",
    "begin",
    "depart",
    "anticipat",
    "wind up",
    "gather",
    "trigger",
)
_FINAL_HOLD_TERMS = ("hold", "lock", "stable", "stabilize", "settle", "freeze")
_DIVERGENCE_TERMS = (
    "diverge",
    "divergence",
    "depart",
    "different middle",
    "leave the opening",
    "away from",
)
_SUPPORT_ONLY_MOTION_TERMS = (
    "camera",
    "lens",
    "zoom",
    "pan",
    "tilt",
    "dolly",
    "framing",
    "reframe",
    "focus",
    "exposure",
    "light",
    "glow",
    "particle",
    "effect",
    "vfx",
    "flare",
    "bloom",
)
_SUBJECT_STATE_MOTION_TERMS = (
    "subject",
    "state",
    "object",
    "entity",
    "character",
    "body",
    "hand",
    "arm",
    "product",
    "target",
    "material",
    "surface",
)
_INFEASIBILITY_TERMS = (
    "infeasible",
    "impossible",
    "cannot",
    "no compatible",
    "no controllable",
    "not executable",
    "contradict",
    "unavailable",
    "absent",
)


def _required_core_behavior_beats(frame_analysis: FrameAnalysis) -> list[ReferenceBehaviorBeat]:
    reference = frame_analysis.reference_video_analysis
    graph = reference.behavior_graph if reference is not None else None
    if graph is None:
        return []
    return [
        beat
        for beat in graph.beats
        if beat.importance == "core" and beat.behavior_type in {"action", "state"}
    ]


def _has_required_arc(plan: FrameAnchoredDirectorPlan) -> bool:
    phases = [window.phase for window in plan.action_arc_windows]
    required = ("preparation", "action", "payoff", "return", "final_lock")
    try:
        positions = [phases.index(phase) for phase in required]
    except ValueError:
        return False
    return positions == sorted(positions) and plan.action_arc_windows[positions[-1]].end_ratio == 1


def _support_only_flattened(
    core_beats: list[ReferenceBehaviorBeat],
    plan: FrameAnchoredDirectorPlan,
) -> bool:
    if not core_beats:
        return False
    execution_windows = [
        window
        for window in plan.action_arc_windows
        if window.phase in {"action", "impact", "payoff"}
    ]
    if not execution_windows:
        return True
    peak_subject = max(window.subject_motion_intensity for window in execution_windows)
    peak_support = max(
        max(window.camera_intensity, window.effect_intensity) for window in execution_windows
    )
    hold_subject = min(
        (
            window.subject_motion_intensity
            for window in plan.action_arc_windows
            if window.phase in {"anchor_hold", "final_lock"}
        ),
        default=0,
    )
    return peak_support > peak_subject and peak_subject <= max(hold_subject, 0.05)


def _has_execution_detail(moment: object) -> bool:
    assigned_beat_id = getattr(moment, "assigned_beat_id", None)
    assigned_core_beat = bool(assigned_beat_id and str(assigned_beat_id).strip())
    adapted_action = str(getattr(moment, "adapted_action", "") or "").strip()
    visible_payoff = str(getattr(moment, "visible_payoff", "") or "").strip()
    return assigned_core_beat and bool(adapted_action) and bool(visible_payoff)


def _endpoint_mismatch_only(*reasons: str) -> bool:
    text = " ".join(reason.casefold() for reason in reasons if reason)
    endpoint = any(term in text for term in ("final", "last frame", "endpoint", "ending"))
    mismatch = any(
        term in text
        for term in ("pose", "position", "orientation", "framing", "composition", "scale")
    )
    infeasible = any(term in text for term in _INFEASIBILITY_TERMS)
    return endpoint and mismatch and not infeasible


def _valid_omission(moment: object) -> bool:
    omission_reason = str(getattr(moment, "omission_reason", "") or "").strip()
    replacement_failure = str(getattr(moment, "equivalent_replacement_failure", "") or "").strip()
    if not omission_reason or not replacement_failure:
        return False
    if _endpoint_mismatch_only(omission_reason, replacement_failure):
        return False
    return any(term in omission_reason.casefold() for term in _INFEASIBILITY_TERMS) and any(
        term in replacement_failure.casefold() for term in _INFEASIBILITY_TERMS
    )


def review_director_action_coverage(
    frame_analysis: FrameAnalysis,
    director_plan: FrameAnchoredDirectorPlan,
) -> DirectorActionCoverageReview:
    core_beats = _required_core_behavior_beats(frame_analysis)
    required_ids = [beat.beat_id for beat in core_beats]
    if not required_ids:
        return DirectorActionCoverageReview(status="pass")

    plan_core_beat_ids = {
        beat.beat_id for beat in director_plan.climax_beats if beat.importance == "core"
    }
    covered_id_set: set[str] = set()
    validly_omitted_id_set: set[str] = set()
    invalid_omission_ids: list[str] = []
    missing_execution_ids: list[str] = []
    missing_return_ids: list[str] = []
    corrections: list[str] = []

    for moment in director_plan.signature_moment_plan:
        referenced_ids = [
            beat_id for beat_id in moment.source_behavior_beat_ids if beat_id in required_ids
        ]
        if not referenced_ids:
            continue
        if moment.strategy == "omit":
            if _valid_omission(moment):
                validly_omitted_id_set.update(referenced_ids)
            else:
                invalid_omission_ids.append(moment.moment_id)
                corrections.append(
                    f"Replace invalid omission {moment.moment_id} with preserve, adapt, or "
                    "an equivalent target action, or prove both execution and equivalent "
                    "replacement are infeasible."
                )
            continue

        has_execution = (
            _has_execution_detail(moment) and (moment.assigned_beat_id or "") in plan_core_beat_ids
        )
        has_return = bool(moment.return_strategy.strip())

        if not has_execution:
            missing_execution_ids.append(moment.moment_id)
            corrections.append(
                "Provide executable subject/state action, assigned core beat, and visible payoff "
                f"for signature moment {moment.moment_id}."
            )
        if not has_return:
            missing_return_ids.append(moment.moment_id)
            corrections.append(
                f"State how signature moment {moment.moment_id} returns continuously to "
                "the exact last anchor."
            )
        if has_execution and has_return:
            covered_id_set.update(referenced_ids)

    covered_ids = [beat_id for beat_id in required_ids if beat_id in covered_id_set]
    omitted_ids = [beat_id for beat_id in required_ids if beat_id in validly_omitted_id_set]
    uncovered_ids = [
        beat_id
        for beat_id in required_ids
        if beat_id not in covered_id_set and beat_id not in validly_omitted_id_set
    ]
    for beat_id in uncovered_ids:
        corrections.append(
            f"Execute uncovered core behavior beat {beat_id} in subject/state motion and "
            "show its visible payoff."
        )

    has_executable_required = bool(set(required_ids) - validly_omitted_id_set)
    if has_executable_required and not _has_required_arc(director_plan):
        corrections.append(
            "Provide dynamically ordered preparation, action, payoff, return, and final_lock "
            "windows ending at ratio 1.0."
        )
    if has_executable_required and not director_plan.final_anchor_return.strip():
        corrections.append("State the continuous final-anchor return before the final lock.")
    if has_executable_required and _support_only_flattened(core_beats, director_plan):
        corrections.append(
            "Increase subject/state motion for the core action; camera or effects alone "
            "cannot execute it."
        )

    return DirectorActionCoverageReview(
        status="corrective" if corrections else "pass",
        required_core_behavior_beat_ids=required_ids,
        covered_core_behavior_beat_ids=covered_ids,
        validly_omitted_core_behavior_beat_ids=omitted_ids,
        uncovered_core_behavior_beat_ids=uncovered_ids,
        invalid_omission_moment_ids=invalid_omission_ids,
        missing_execution_detail_moment_ids=missing_execution_ids,
        missing_return_moment_ids=missing_return_ids,
        correction_requirements=list(dict.fromkeys(corrections)),
    )


def _scene_has_subject_execution(scene: FrameAnchoredStoryboardScene) -> bool:
    motion = (scene.motion or "").strip().casefold()
    if not motion:
        return False
    has_support_only_cue = any(term in motion for term in _SUPPORT_ONLY_MOTION_TERMS)
    if not has_support_only_cue:
        return True
    return any(term in motion for term in _SUBJECT_STATE_MOTION_TERMS)


def _scene_text(scene: FrameAnchoredStoryboardScene) -> str:
    return " ".join(
        value.strip()
        for value in (
            scene.visual,
            scene.motion or "",
            scene.transition_goal or "",
            scene.anchor_return_instruction or "",
            scene.anti_flattening_requirement or "",
        )
        if value and value.strip()
    ).casefold()


def _scene_has_preparation(scene: FrameAnchoredStoryboardScene) -> bool:
    text = _scene_text(scene)
    return any(term in text for term in _PREPARATION_TERMS)


def _scene_has_final_hold(scene: FrameAnchoredStoryboardScene) -> bool:
    if scene.frame_anchor != "last_frame":
        return False
    text = _scene_text(scene)
    return any(term in text for term in _FINAL_HOLD_TERMS)


def _scene_has_temporary_divergence(scene: FrameAnchoredStoryboardScene) -> bool:
    if scene.frame_anchor == "transition" and _scene_has_subject_execution(scene):
        return True
    text = _scene_text(scene)
    return any(term in text for term in _DIVERGENCE_TERMS)


def _validate_timeline(storyboard: FrameAnchoredStoryboard) -> None:
    scenes = storyboard.scenes
    if any(scene.start_second is None or scene.end_second is None for scene in scenes):
        raise ValueError("storyboard scenes require explicit start and end times")

    first_start = float(scenes[0].start_second)
    if abs(first_start) > _TIMELINE_TOLERANCE_SECONDS:
        raise ValueError("first scene must start at 0")
    final_end = float(scenes[-1].end_second)
    if abs(final_end - storyboard.duration_seconds) > _TIMELINE_TOLERANCE_SECONDS:
        raise ValueError("final scene must end at requested duration")

    for scene in scenes:
        start = float(scene.start_second)
        end = float(scene.end_second)
        if start < -_TIMELINE_TOLERANCE_SECONDS or end > (
            storyboard.duration_seconds + _TIMELINE_TOLERANCE_SECONDS
        ):
            raise ValueError("storyboard scene timing must stay within requested duration")
        if end - start <= _TIMELINE_TOLERANCE_SECONDS:
            raise ValueError("storyboard scene timing must be ordered within requested duration")

    for previous, current in zip(scenes, scenes[1:], strict=False):
        delta = float(current.start_second) - float(previous.end_second)
        if delta > _TIMELINE_TOLERANCE_SECONDS:
            raise ValueError("storyboard timeline has a gap")
        if delta < -_TIMELINE_TOLERANCE_SECONDS:
            raise ValueError("storyboard timeline has an overlap")


def validate_final_storyboard_action_coverage(
    storyboard: FrameAnchoredStoryboard,
    frame_analysis: FrameAnalysis,
    review: DirectorActionCoverageReview,
) -> None:
    plan = frame_analysis.director_plan
    if plan is None:
        return

    _validate_timeline(storyboard)
    required_ids = [beat.beat_id for beat in _required_core_behavior_beats(frame_analysis)]
    required_id_set = set(required_ids)
    validly_omitted_ids = {
        beat_id
        for moment in plan.signature_moment_plan
        if moment.strategy == "omit" and _valid_omission(moment)
        for beat_id in moment.source_behavior_beat_ids
        if beat_id in required_id_set
    }
    validly_omitted_ids.intersection_update(review.validly_omitted_core_behavior_beat_ids)
    executable_required_ids = required_id_set - validly_omitted_ids

    scene_by_signature: dict[str, list[FrameAnchoredStoryboardScene]] = {}
    for scene in storyboard.scenes:
        for moment_id in scene.signature_moment_ids:
            scene_by_signature.setdefault(moment_id, []).append(scene)

    executed_source_ids: set[str] = set()
    execution_scene_indexes: list[int] = []
    for moment in plan.signature_moment_plan:
        if moment.strategy == "omit":
            continue
        scenes = scene_by_signature.get(moment.moment_id, [])
        if not scenes:
            raise ValueError(f"storyboard is missing required signature moment: {moment.moment_id}")

        referenced_required_ids = required_id_set.intersection(moment.source_behavior_beat_ids)
        if referenced_required_ids:
            execution_scenes = [
                scene
                for scene in scenes
                if _scene_has_subject_execution(scene)
                and referenced_required_ids.intersection(scene.source_behavior_beat_ids)
            ]
        else:
            execution_scenes = [scene for scene in scenes if _scene_has_subject_execution(scene)]
        requires_execution = bool(referenced_required_ids) or moment.transfer_role in {
            "primary_action",
            "interaction",
            "impact",
        }
        if requires_execution and not execution_scenes:
            raise ValueError(f"signature moment {moment.moment_id} lacks subject/state execution")
        for scene in execution_scenes:
            execution_scene_indexes.append(storyboard.scenes.index(scene))
            if moment.temporary_divergence.strip() and not _scene_has_temporary_divergence(scene):
                continue
            executed_source_ids.update(
                referenced_required_ids.intersection(scene.source_behavior_beat_ids)
            )

        if moment.moment_type in {"camera", "combined"} and not any(
            (scene.camera_instruction or "").strip() for scene in scenes
        ):
            raise ValueError(f"signature moment {moment.moment_id} lacks camera support")
        if moment.moment_type in {"effect", "combined"} and not any(
            (scene.effect_timing or "").strip() for scene in scenes
        ):
            raise ValueError(f"signature moment {moment.moment_id} lacks effect support")
        if not any((scene.action_result_requirement or "").strip() for scene in scenes):
            raise ValueError(f"signature moment {moment.moment_id} lacks visible payoff")
        if moment.temporary_divergence.strip() and not any(
            _scene_has_temporary_divergence(scene) for scene in scenes
        ):
            raise ValueError(f"signature moment {moment.moment_id} lacks temporary divergence")

        if not any((scene.anchor_return_instruction or "").strip() for scene in scenes):
            raise ValueError(f"signature moment {moment.moment_id} lacks linked anchor return")

    missing_source_ids = executable_required_ids - executed_source_ids
    if missing_source_ids:
        raise ValueError(
            "storyboard is missing executed source behavior beats: "
            + ", ".join(sorted(missing_source_ids))
        )

    if executable_required_ids:
        latest_execution_index = max(execution_scene_indexes, default=0)
        if not any(
            _scene_has_preparation(scene)
            for scene in storyboard.scenes[: latest_execution_index + 1]
        ):
            raise ValueError("storyboard is missing preparation evidence")
        if not any((scene.anchor_return_instruction or "").strip() for scene in storyboard.scenes):
            raise ValueError("storyboard is missing anchor return instruction")
        if not _scene_has_final_hold(storyboard.scenes[-1]):
            raise ValueError("storyboard is missing final hold evidence")
