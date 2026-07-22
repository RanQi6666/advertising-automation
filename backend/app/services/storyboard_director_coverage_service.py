from __future__ import annotations

from backend.app.schemas.ai import (
    DirectorActionCoverageReview,
    FrameAnalysis,
    FrameAnchoredDirectorPlan,
    FrameAnchoredStoryboard,
    FrameAnchoredStoryboardScene,
    ReferenceBehaviorBeat,
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
    if not {"action", "return", "final_lock"}.issubset(phases):
        return False
    action = phases.index("action")
    returning = phases.index("return")
    lock = phases.index("final_lock")
    return action < returning < lock and plan.action_arc_windows[lock].end_ratio == 1


def _support_only_flattened(
    core_beats: list[ReferenceBehaviorBeat],
    plan: FrameAnchoredDirectorPlan,
) -> bool:
    if not any(beat.behavior_type == "action" for beat in core_beats):
        return False
    action_windows = [
        window for window in plan.action_arc_windows if window.phase in {"action", "impact"}
    ]
    if not action_windows:
        return True
    peak_subject = max(window.subject_motion_intensity for window in action_windows)
    peak_support = max(
        max(window.camera_intensity, window.effect_intensity) for window in action_windows
    )
    hold_subject = min(
        (
            window.subject_motion_intensity
            for window in plan.action_arc_windows
            if window.phase in {"anchor_hold", "final_lock"}
        ),
        default=0,
    )
    return peak_support > peak_subject and peak_subject <= hold_subject


def _has_execution_detail(moment: object) -> bool:
    assigned_beat_id = getattr(moment, "assigned_beat_id", None)
    assigned_core_beat = bool(assigned_beat_id and str(assigned_beat_id).strip())
    adapted_action = str(getattr(moment, "adapted_action", "") or "").strip()
    visible_payoff = str(getattr(moment, "visible_payoff", "") or "").strip()
    return assigned_core_beat and bool(adapted_action) and bool(visible_payoff)


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
            invalid_omission_ids.append(moment.moment_id)
            corrections.append(
                f"Replace invalid omission {moment.moment_id} with preserve, adapt, or "
                "an equivalent target action."
            )
            continue

        has_execution = (
            _has_execution_detail(moment)
            and (moment.assigned_beat_id or "") in plan_core_beat_ids
        )
        has_return = bool(moment.return_strategy.strip())

        if not has_execution:
            missing_execution_ids.append(moment.moment_id)
            corrections.append(
                "Provide executable action, assigned core beat, and visible payoff for "
                f"signature moment {moment.moment_id}."
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
    uncovered_ids = [beat_id for beat_id in required_ids if beat_id not in covered_id_set]
    for beat_id in uncovered_ids:
        corrections.append(
            f"Execute uncovered core behavior beat {beat_id} in subject/state motion and "
            "show its visible payoff."
        )

    if required_ids and not _has_required_arc(director_plan):
        corrections.append(
            "Provide dynamically ordered action, return, and final_lock windows ending "
            "at ratio 1.0."
        )
    if required_ids and not director_plan.final_anchor_return.strip():
        corrections.append("State the continuous final-anchor return before the final lock.")
    if _support_only_flattened(core_beats, director_plan):
        corrections.append(
            "Increase subject/state motion for the core action; camera or effects alone "
            "cannot execute it."
        )

    return DirectorActionCoverageReview(
        status="corrective" if corrections else "pass",
        required_core_behavior_beat_ids=required_ids,
        covered_core_behavior_beat_ids=covered_ids,
        uncovered_core_behavior_beat_ids=uncovered_ids,
        invalid_omission_moment_ids=invalid_omission_ids,
        missing_execution_detail_moment_ids=missing_execution_ids,
        missing_return_moment_ids=missing_return_ids,
        correction_requirements=list(dict.fromkeys(corrections)),
    )


def _scene_has_execution(scene: FrameAnchoredStoryboardScene) -> bool:
    return bool((scene.motion or "").strip() or (scene.action_result_requirement or "").strip())


def validate_final_storyboard_action_coverage(
    storyboard: FrameAnchoredStoryboard,
    frame_analysis: FrameAnalysis,
    review: DirectorActionCoverageReview,
) -> None:
    plan = frame_analysis.director_plan
    if plan is None:
        return

    scene_by_signature: dict[str, list[FrameAnchoredStoryboardScene]] = {}
    for scene in storyboard.scenes:
        for moment_id in scene.signature_moment_ids:
            scene_by_signature.setdefault(moment_id, []).append(scene)

    for moment in plan.signature_moment_plan:
        if moment.strategy == "omit":
            continue
        scenes = scene_by_signature.get(moment.moment_id, [])
        if not scenes:
            raise ValueError(f"storyboard is missing required signature moment: {moment.moment_id}")
        if moment.transfer_role in {"primary_action", "interaction", "impact"} and not any(
            _scene_has_execution(scene) for scene in scenes
        ):
            raise ValueError(
                f"signature moment {moment.moment_id} lacks executable action or result direction"
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

    covered_source_ids = {
        beat_id for scene in storyboard.scenes for beat_id in scene.source_behavior_beat_ids
    }
    covered_source_ids.update(
        beat_id
        for moment in plan.signature_moment_plan
        if moment.strategy != "omit" and moment.moment_id in scene_by_signature
        for beat_id in moment.source_behavior_beat_ids
    )
    missing_source_ids = set(review.required_core_behavior_beat_ids) - covered_source_ids
    if missing_source_ids:
        raise ValueError(
            "storyboard is missing required source behavior beats: "
            + ", ".join(sorted(missing_source_ids))
        )

    divergent = any(
        moment.strategy != "omit" and moment.temporary_divergence.strip()
        for moment in plan.signature_moment_plan
    )
    if divergent and not any(
        (scene.anchor_return_instruction or "").strip() for scene in storyboard.scenes[:-1]
    ):
        raise ValueError("storyboard is missing anchor return instruction")

    if storyboard.scenes[-1].end_second != storyboard.duration_seconds:
        raise ValueError("final scene must end at requested duration")
    for scene in storyboard.scenes:
        if scene.start_second is None or scene.end_second is None:
            continue
        if not 0 <= scene.start_second < scene.end_second <= storyboard.duration_seconds:
            raise ValueError("storyboard scene timing must be ordered within requested duration")
