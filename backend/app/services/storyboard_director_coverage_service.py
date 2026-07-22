from __future__ import annotations

import re

from backend.app.schemas.ai import (
    DirectorActionCorrection,
    DirectorActionCoverageReview,
    FrameAnalysis,
    FrameAnchoredDirectorPlan,
    FrameAnchoredStoryboard,
    FrameAnchoredStoryboardScene,
    ReferenceBehaviorBeat,
    omission_infeasibility_matches_category,
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
_NEGATED_EXECUTION_PATTERN = re.compile(
    r"\b(?:no|not|never|nothing|neither|nor|without)\b|n['?]t\b"
)
_SUBJECT_EXECUTION_PATTERNS = (
    r"\bexecut(?:e|es|ed|ing)\b",
    r"\bperform(?:s|ed|ing)?\b",
    r"\bcomplet(?:e|es|ed|ing)\b",
    r"\bchang(?:e|es|ed|ing)\b",
    r"\btransform(?:s|ed|ing)?\b",
    r"\bmov(?:e|es|ed|ing)\b",
    r"\badvanc(?:e|es|ed|ing)\b",
    r"\bretreat(?:s|ed|ing)?\b",
    r"\bturn(?:s|ed|ing)?\b",
    r"\brotat(?:e|es|ed|ing)\b",
    r"\brais(?:e|es|ed|ing)\b",
    r"\blower(?:s|ed|ing)?\b",
    r"\breach(?:es|ed|ing)?\b",
    r"\bstep(?:s|ped|ping)?\b",
    r"\bopen(?:s|ed|ing)?\b",
    r"\bclos(?:e|es|ed|ing)\b",
    r"\bexpand(?:s|ed|ing)?\b",
    r"\bcontract(?:s|ed|ing)?\b",
    r"\bdeform(?:s|ed|ing)?\b",
    r"\bshift(?:s|ed|ing)?\b",
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


def _valid_omission(moment: object) -> bool:
    omission_reason = str(getattr(moment, "omission_reason", "") or "").strip()
    replacement_failure = str(getattr(moment, "equivalent_replacement_failure", "") or "").strip()
    literal_category = getattr(moment, "literal_infeasibility_category", None)
    literal_evidence = str(
        getattr(moment, "literal_infeasibility_evidence", "") or ""
    ).strip()
    equivalent_category = getattr(moment, "equivalent_infeasibility_category", None)
    equivalent_evidence = str(
        getattr(moment, "equivalent_infeasibility_evidence", "") or ""
    ).strip()
    return bool(
        omission_infeasibility_matches_category(
            literal_category,
            omission_reason,
            literal_evidence,
            literal=True,
        )
        and omission_infeasibility_matches_category(
            equivalent_category,
            replacement_failure,
            equivalent_evidence,
            literal=False,
        )
    )


def review_director_action_coverage(
    frame_analysis: FrameAnalysis,
    director_plan: FrameAnchoredDirectorPlan,
) -> DirectorActionCoverageReview:
    core_beats = _required_core_behavior_beats(frame_analysis)
    required_ids = [beat.beat_id for beat in core_beats]
    if not required_ids:
        return DirectorActionCoverageReview(status="pass")

    linked_required_ids = {
        source_id
        for moment in director_plan.signature_moment_plan
        for source_id in moment.source_behavior_beat_ids
        if source_id in required_ids
    }
    unlinked_ids = [beat_id for beat_id in required_ids if beat_id not in linked_required_ids]
    if unlinked_ids:
        return DirectorActionCoverageReview(
            status="unrecoverable",
            required_core_behavior_beat_ids=required_ids,
            uncovered_core_behavior_beat_ids=unlinked_ids,
            unrecoverable_reasons=[
                f"Core behavior beat {beat_id} has no signature/source linkage."
                for beat_id in unlinked_ids
            ],
        )

    plan_core_beat_ids = {
        beat.beat_id for beat in director_plan.climax_beats if beat.importance == "core"
    }
    covered_id_set: set[str] = set()
    validly_omitted_id_set: set[str] = set()
    invalid_omission_ids: list[str] = []
    missing_execution_ids: list[str] = []
    missing_return_ids: list[str] = []
    corrections: list[str] = []
    structured_corrections: list[DirectorActionCorrection] = []

    def add_structured_correction(
        *,
        correction_type: str,
        moment_ids: list[str],
        source_ids: list[str],
        instruction: str,
    ) -> None:
        if not moment_ids or not source_ids:
            return
        correction = DirectorActionCorrection(
            correction_type=correction_type,
            signature_moment_ids=list(dict.fromkeys(moment_ids)),
            source_behavior_beat_ids=list(dict.fromkeys(source_ids)),
            instruction=instruction,
        )
        if correction not in structured_corrections:
            structured_corrections.append(correction)

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
            continue

        assigned_core_beat = (moment.assigned_beat_id or "") in plan_core_beat_ids
        has_action = bool(moment.adapted_action.strip()) and assigned_core_beat
        has_payoff = bool(moment.visible_payoff.strip())
        has_execution = has_action and has_payoff
        has_return = bool(moment.return_strategy.strip())

        if not has_execution:
            missing_execution_ids.append(moment.moment_id)
            corrections.append(
                "Provide executable subject/state action, assigned core beat, and visible payoff "
                f"for signature moment {moment.moment_id}."
            )
            correction_type = "payoff" if has_action and not has_payoff else "execution"
            add_structured_correction(
                correction_type=correction_type,
                moment_ids=[moment.moment_id],
                source_ids=referenced_ids,
                instruction=(
                    "Provide a visible payoff after the linked action executes."
                    if correction_type == "payoff"
                    else "Provide executable subject/state action with an assigned core beat."
                ),
            )
        if not has_return:
            missing_return_ids.append(moment.moment_id)
            corrections.append(
                f"State how signature moment {moment.moment_id} returns continuously to "
                "the exact last anchor."
            )
            add_structured_correction(
                correction_type="return",
                moment_ids=[moment.moment_id],
                source_ids=referenced_ids,
                instruction="Return continuously from the linked payoff to the exact last anchor.",
            )
        if has_execution and has_return:
            covered_id_set.update(referenced_ids)

    if invalid_omission_ids:
        invalid_source_ids = {
            source_id
            for moment in director_plan.signature_moment_plan
            if moment.moment_id in invalid_omission_ids
            for source_id in moment.source_behavior_beat_ids
            if source_id in required_ids
        }
        return DirectorActionCoverageReview(
            status="unrecoverable",
            required_core_behavior_beat_ids=required_ids,
            covered_core_behavior_beat_ids=[
                beat_id for beat_id in required_ids if beat_id in covered_id_set
            ],
            validly_omitted_core_behavior_beat_ids=[
                beat_id for beat_id in required_ids if beat_id in validly_omitted_id_set
            ],
            uncovered_core_behavior_beat_ids=[
                beat_id for beat_id in required_ids if beat_id in invalid_source_ids
            ],
            invalid_omission_moment_ids=invalid_omission_ids,
            unrecoverable_reasons=[
                f"Signature moment {moment_id} has an invalid omission contract that cannot "
                "be repaired by storyboard generation."
                for moment_id in invalid_omission_ids
            ],
        )

    covered_ids = [beat_id for beat_id in required_ids if beat_id in covered_id_set]
    omitted_ids = [beat_id for beat_id in required_ids if beat_id in validly_omitted_id_set]
    uncovered_ids = [
        beat_id
        for beat_id in required_ids
        if beat_id not in covered_id_set and beat_id not in validly_omitted_id_set
    ]

    has_executable_required = bool(set(required_ids) - validly_omitted_id_set)
    correction_moments = [
        moment
        for moment in director_plan.signature_moment_plan
        if set(moment.source_behavior_beat_ids).intersection(required_ids)
        and not (moment.strategy == "omit" and _valid_omission(moment))
    ]
    correction_moment_ids = [moment.moment_id for moment in correction_moments]
    correction_source_ids = [
        source_id
        for moment in correction_moments
        for source_id in moment.source_behavior_beat_ids
        if source_id in required_ids
    ]
    if has_executable_required and not _has_required_arc(director_plan):
        instruction = (
            "Provide dynamically ordered preparation, action, payoff, return, and final_lock "
            "windows ending at ratio 1.0."
        )
        corrections.append(instruction)
        add_structured_correction(
            correction_type="action_arc",
            moment_ids=correction_moment_ids,
            source_ids=correction_source_ids,
            instruction=instruction,
        )
    if has_executable_required and not director_plan.final_anchor_return.strip():
        instruction = "State the continuous final-anchor return before the final lock."
        corrections.append(instruction)
        add_structured_correction(
            correction_type="return",
            moment_ids=correction_moment_ids,
            source_ids=correction_source_ids,
            instruction=instruction,
        )
    if has_executable_required and _support_only_flattened(core_beats, director_plan):
        instruction = (
            "Increase subject/state motion for the core action; camera or effects alone "
            "cannot execute it."
        )
        corrections.append(instruction)
        add_structured_correction(
            correction_type="execution",
            moment_ids=correction_moment_ids,
            source_ids=correction_source_ids,
            instruction=instruction,
        )

    return DirectorActionCoverageReview(
        status="corrective" if corrections or structured_corrections else "pass",
        required_core_behavior_beat_ids=required_ids,
        covered_core_behavior_beat_ids=covered_ids,
        validly_omitted_core_behavior_beat_ids=omitted_ids,
        uncovered_core_behavior_beat_ids=uncovered_ids,
        invalid_omission_moment_ids=invalid_omission_ids,
        missing_execution_detail_moment_ids=missing_execution_ids,
        missing_return_moment_ids=missing_return_ids,
        correction_requirements=list(dict.fromkeys(corrections)),
        structured_corrections=structured_corrections,
    )


_ACTOR_AUXILIARY_PATTERN = re.compile(
    r"\b(?:do|does|did|is|are|was|were|has|have|had|will|would|shall|should|"
    r"can|could|may|might|must)\b"
)
_PREDICATE_COORDINATOR_PATTERN = re.compile(r"\b(?:and|but|then|or)\b")
_ACTOR_MODIFIER_BOUNDARY_PATTERN = re.compile(
    r"\b(?:near|beside|behind|before|after|around|inside|outside|under|over|"
    r"against|toward|towards|from|with|without|next\s+to|in\s+front\s+of)\b"
)
_NON_ACTOR_TOKENS = {
    "a",
    "an",
    "the",
    "this",
    "that",
    "these",
    "those",
    "my",
    "our",
    "your",
    "his",
    "her",
    "its",
    "their",
    "no",
    "not",
    "never",
    "nothing",
    "neither",
    "nor",
    "without",
    "still",
}
_SUPPORT_ONLY_ACTOR_TERMS = set(_SUPPORT_ONLY_MOTION_TERMS) | {
    "composition",
    "frame",
    "shot",
    "background",
    "lighting",
}


def _latest_predicate_coordinator(
    clause: str,
    predicate_start: int,
) -> re.Match[str] | None:
    boundary = None
    for match in _PREDICATE_COORDINATOR_PATTERN.finditer(clause[:predicate_start]):
        boundary = match
    return boundary


def _actor_phrase_from_scope(actor_scope: str) -> str | None:
    actor_scope = actor_scope.strip()
    if not actor_scope:
        return None
    auxiliary = _ACTOR_AUXILIARY_PATTERN.search(actor_scope)
    if auxiliary is not None:
        actor_scope = actor_scope[: auxiliary.start()].strip()
    tokens = re.findall(r"[a-z][a-z'-]*", actor_scope)
    candidates = [
        token
        for token in tokens
        if token not in _NON_ACTOR_TOKENS and not token.endswith("ly")
    ]
    return " ".join(candidates) or None


def _predicate_context(
    clause: str,
    predicate_start: int,
    previous_actor_phrase: str | None,
    previous_predicate_negated: bool,
) -> tuple[str | None, bool]:
    boundary = _latest_predicate_coordinator(clause, predicate_start)
    scope_start = boundary.end() if boundary is not None else 0
    local_scope = clause[scope_start:predicate_start]
    local_actor_phrase = _actor_phrase_from_scope(local_scope)
    if local_actor_phrase is not None:
        actor_phrase = local_actor_phrase
    elif previous_actor_phrase is not None:
        actor_phrase = previous_actor_phrase
    elif boundary is not None:
        actor_phrase = _actor_phrase_from_scope(clause[: boundary.start()])
    else:
        actor_phrase = None

    locally_negated = _NEGATED_EXECUTION_PATTERN.search(local_scope) is not None
    if locally_negated or local_actor_phrase is not None or boundary is None:
        return actor_phrase, locally_negated
    coordinator = boundary.group(0)
    inherits_negation = coordinator in {"and", "or"} and previous_actor_phrase is not None
    return actor_phrase, previous_predicate_negated if inherits_negation else False


def _is_support_only_actor(actor_phrase: str) -> bool:
    actor_core = _ACTOR_MODIFIER_BOUNDARY_PATTERN.split(actor_phrase, maxsplit=1)[0]
    tokens = re.findall(r"[a-z][a-z'-]*", actor_core)
    for token in tokens:
        singular = token[:-1] if token.endswith("s") else token
        if token in _SUPPORT_ONLY_ACTOR_TERMS or singular in _SUPPORT_ONLY_ACTOR_TERMS:
            return True
    return False


def _scene_has_subject_execution(scene: FrameAnchoredStoryboardScene) -> bool:
    motion = (scene.motion or "").strip().casefold()
    if not motion:
        return False
    clauses = [
        clause.strip()
        for clause in re.split(r"[.;]|\b(?:while|whereas|as)\b", motion)
        if clause.strip()
    ]
    for clause in clauses:
        execution_matches = sorted(
            (
                match
                for pattern in _SUBJECT_EXECUTION_PATTERNS
                for match in re.finditer(pattern, clause)
            ),
            key=lambda match: match.start(),
        )
        if not execution_matches:
            continue
        previous_actor_phrase: str | None = None
        previous_predicate_negated = False
        for match in execution_matches:
            actor_phrase, predicate_negated = _predicate_context(
                clause,
                match.start(),
                previous_actor_phrase,
                previous_predicate_negated,
            )
            if actor_phrase is not None:
                previous_actor_phrase = actor_phrase
            previous_predicate_negated = predicate_negated
            if (
                actor_phrase is not None
                and not _is_support_only_actor(actor_phrase)
                and not predicate_negated
            ):
                return True
    return False


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


def _scene_links_moment(
    scene: FrameAnchoredStoryboardScene,
    moment_id: str,
    source_ids: set[str],
) -> bool:
    del source_ids
    return moment_id in scene.signature_moment_ids


def _phase_window_seconds(
    plan: FrameAnchoredDirectorPlan, phase: str, duration_seconds: float
) -> float:
    intervals = sorted(
        (window.start_ratio, window.end_ratio)
        for window in plan.action_arc_windows
        if window.phase == phase
    )
    covered_ratio = 0.0
    current_start: float | None = None
    current_end: float | None = None
    for start_ratio, end_ratio in intervals:
        if current_start is None:
            current_start, current_end = start_ratio, end_ratio
        elif start_ratio <= float(current_end):
            current_end = max(float(current_end), end_ratio)
        else:
            covered_ratio += float(current_end) - current_start
            current_start, current_end = start_ratio, end_ratio
    if current_start is not None and current_end is not None:
        covered_ratio += current_end - current_start
    return covered_ratio * duration_seconds


def _phase_compression_required_for_duration(
    duration_seconds: float,
    source_ids: set[str],
    frame_analysis: FrameAnalysis,
    plan: FrameAnchoredDirectorPlan,
) -> bool:
    beats_by_id = {
        beat.beat_id: beat for beat in _required_core_behavior_beats(frame_analysis)
    }
    beats = [beats_by_id[beat_id] for beat_id in source_ids if beat_id in beats_by_id]
    if not beats:
        return False

    duration = float(duration_seconds)
    beat_count = len(beats)
    base_readability = (
        sum(beat.minimum_readable_duration_seconds for beat in beats) / beat_count
    )
    action_readability = sum(beat.minimum_readable_duration_seconds for beat in beats)
    preparation_units = sum(len(beat.depends_on) for beat in beats) + sum(
        beat.behavior_type == "state" for beat in beats
    )
    payoff_readability = (
        sum(
            beat.minimum_readable_duration_seconds
            * max(len(beat.visible_evidence), 1)
            for beat in beats
        )
        / beat_count
    )

    camera_units = 0
    effect_units = 0
    reference = frame_analysis.reference_video_analysis
    if reference is not None:
        intensity_rank = {"low": 0, "medium": 1, "high": 2}
        for segment in reference.segments:
            movement = segment.camera.movement.strip().casefold()
            if movement and movement not in {"none", "static", "locked", "stationary"}:
                camera_units += 1
            camera_units += intensity_rank.get(segment.camera.intensity.casefold(), 0)
            effect_units += len([effect for effect in segment.effects if effect.strip()])

    endpoint_units = sum(
        first_value != last_value
        for first_value, last_value in (
            (
                frame_analysis.first_frame.composition.strip().casefold(),
                frame_analysis.last_frame.composition.strip().casefold(),
            ),
            (
                frame_analysis.first_frame.camera_perspective.strip().casefold(),
                frame_analysis.last_frame.camera_perspective.strip().casefold(),
            ),
            (
                tuple(
                    item.strip().casefold()
                    for item in frame_analysis.first_frame.visible_subjects
                ),
                tuple(
                    item.strip().casefold()
                    for item in frame_analysis.last_frame.visible_subjects
                ),
            ),
        )
    )
    continuity_units = len(frame_analysis.transition_brief.continuity_requirements)
    required_seconds = {
        "preparation": base_readability * preparation_units / beat_count,
        "action": action_readability
        + base_readability * max(camera_units - beat_count, 0) / beat_count,
        "payoff": max(
            payoff_readability,
            base_readability * effect_units / beat_count,
        ),
        "return": base_readability * endpoint_units / beat_count,
        "final_lock": base_readability * continuity_units / beat_count,
    }
    return any(
        required > _phase_window_seconds(plan, phase, duration)
        + _TIMELINE_TOLERANCE_SECONDS
        for phase, required in required_seconds.items()
    )


def _phase_compression_required(
    storyboard: FrameAnchoredStoryboard,
    source_ids: set[str],
    frame_analysis: FrameAnalysis,
    plan: FrameAnchoredDirectorPlan,
) -> bool:
    return _phase_compression_required_for_duration(
        float(storyboard.duration_seconds), source_ids, frame_analysis, plan
    )


def _director_phases_overlap(plan: FrameAnchoredDirectorPlan, left: str, right: str) -> bool:
    left_windows = [window for window in plan.action_arc_windows if window.phase == left]
    right_windows = [window for window in plan.action_arc_windows if window.phase == right]
    return any(
        max(left_window.start_ratio, right_window.start_ratio)
        < min(left_window.end_ratio, right_window.end_ratio)
        for left_window in left_windows
        for right_window in right_windows
    )


def _validate_moment_phase_order(
    *,
    storyboard: FrameAnchoredStoryboard,
    frame_analysis: FrameAnalysis,
    plan: FrameAnchoredDirectorPlan,
    moment_id: str,
    source_ids: set[str],
    linked_scene_indexes: list[int],
    execution_scene_indexes: list[int],
    final_hold_index: int,
) -> None:
    preparation_indexes = [
        index
        for index, scene in enumerate(storyboard.scenes)
        if _scene_has_preparation(scene)
        and _scene_links_moment(scene, moment_id, source_ids)
    ]
    payoff_indexes = [
        index
        for index in linked_scene_indexes
        if (storyboard.scenes[index].action_result_requirement or "").strip()
    ]
    return_indexes = [
        index
        for index in linked_scene_indexes
        if (storyboard.scenes[index].anchor_return_instruction or "").strip()
    ]
    if not preparation_indexes:
        raise ValueError(
            f"storyboard is missing preparation evidence for signature moment {moment_id}"
        )
    if not payoff_indexes:
        raise ValueError(f"signature moment {moment_id} lacks visible payoff")
    if not return_indexes:
        raise ValueError(f"signature moment {moment_id} lacks linked anchor return")

    phase_indexes = (
        min(preparation_indexes),
        min(execution_scene_indexes),
        min(payoff_indexes),
        min(return_indexes),
    )
    if not (
        phase_indexes[0] <= phase_indexes[1] <= phase_indexes[2] <= phase_indexes[3]
    ):
        raise ValueError(f"signature moment {moment_id} has invalid phase order")

    compression_required = _phase_compression_required(
        storyboard, source_ids, frame_analysis, plan
    )
    phase_names = ("preparation", "action", "payoff", "return")
    for index, (left_index, right_index) in enumerate(
        zip(phase_indexes, phase_indexes[1:], strict=False)
    ):
        if left_index != right_index:
            continue
        if compression_required or _director_phases_overlap(
            plan, phase_names[index], phase_names[index + 1]
        ):
            continue
        raise ValueError(
            f"signature moment {moment_id} uses unsupported same-scene phase sharing"
        )
    return_and_hold_may_share = compression_required or _director_phases_overlap(
        plan, "return", "final_lock"
    )
    if phase_indexes[3] > final_hold_index or (
        phase_indexes[3] == final_hold_index and not return_and_hold_may_share
    ):
        raise ValueError(f"signature moment {moment_id} return must precede final hold")


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
    final_hold_index = len(storyboard.scenes) - 1
    for moment in plan.signature_moment_plan:
        if moment.strategy == "omit":
            continue
        scenes = scene_by_signature.get(moment.moment_id, [])
        if not scenes:
            raise ValueError(f"storyboard is missing required signature moment: {moment.moment_id}")

        referenced_required_ids = required_id_set.intersection(moment.source_behavior_beat_ids)
        linked_scene_indexes = [
            index
            for index, scene in enumerate(storyboard.scenes)
            if _scene_links_moment(scene, moment.moment_id, referenced_required_ids)
        ]
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
        moment_execution_indexes = [storyboard.scenes.index(scene) for scene in execution_scenes]
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
        if moment.temporary_divergence.strip() and not any(
            _scene_has_temporary_divergence(scene) for scene in scenes
        ):
            raise ValueError(f"signature moment {moment.moment_id} lacks temporary divergence")

        if requires_execution:
            _validate_moment_phase_order(
                storyboard=storyboard,
                frame_analysis=frame_analysis,
                plan=plan,
                moment_id=moment.moment_id,
                source_ids=executable_required_ids,
                linked_scene_indexes=linked_scene_indexes,
                execution_scene_indexes=moment_execution_indexes,
                final_hold_index=final_hold_index,
            )

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
