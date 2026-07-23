from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from typing import Any

from backend.app.schemas.ai import (
    DirectorTensionStage,
    FrameAnalysis,
    FrameAnchoredStoryboard,
    FrameAnchoredStoryboardDraft,
    FrameAnchoredStoryboardDraftScene,
    FrameAnchoredStoryboardScene,
    StoryboardDraftExecutionAction,
    StoryboardExecutionEvidence,
    StoryboardPhaseEvidence,
)

logger = logging.getLogger(__name__)

_VALID_TENSION_STAGES = {"setup", "trigger", "escalation", "climax", "resolution"}
_ACTION_PHASE_TO_TENSION: dict[str, DirectorTensionStage] = {
    "anchor_hold": "setup",
    "departure": "setup",
    "preparation": "setup",
    "action": "escalation",
    "impact": "climax",
    "payoff": "resolution",
    "return": "resolution",
    "final_lock": "resolution",
}


def compile_storyboard_v2(
    draft: FrameAnchoredStoryboardDraft | FrameAnchoredStoryboard,
    frame_analysis: FrameAnalysis,
    duration_seconds: int,
    aspect_ratio: str,
) -> FrameAnchoredStoryboard:
    """Compile an LLM creative draft into the strict private storyboard contract."""
    if duration_seconds <= 0:
        raise ValueError("storyboard target duration must be positive")

    normalized_draft = _coerce_draft(draft)
    if len(normalized_draft.scenes) < 2:
        raise ValueError("storyboard draft requires at least two scenes")

    plan = frame_analysis.director_plan
    valid_moments = {
        moment.moment_id.strip(): moment
        for moment in (plan.signature_moment_plan if plan is not None else [])
        if moment.strategy != "omit" and moment.moment_id.strip()
    }
    valid_beats = {
        beat.beat_id.strip()
        for beat in (plan.climax_beats if plan is not None else [])
        if beat.beat_id.strip()
    }
    reference = frame_analysis.reference_video_analysis
    graph = reference.behavior_graph if reference is not None else None
    valid_sources = {
        beat.beat_id.strip()
        for beat in (graph.beats if graph is not None else [])
        if beat.beat_id.strip()
    }
    if not valid_sources:
        valid_sources = {
            source_id.strip()
            for moment in valid_moments.values()
            for source_id in moment.source_behavior_beat_ids
            if source_id.strip()
        }

    boundaries = _compile_scene_boundaries(
        normalized_draft.scenes,
        duration_seconds=float(duration_seconds),
    )
    unknown_moments: set[str] = set()
    unknown_sources: set[str] = set()
    unknown_beats: set[str] = set()
    claims: dict[tuple[object, ...], str] = {}
    compiled_scenes: list[FrameAnchoredStoryboardScene] = []

    for index, draft_scene in enumerate(normalized_draft.scenes):
        moment_ids = _filter_known_ids(
            draft_scene.signature_moment_ids,
            valid_moments,
            unknown_moments,
        )
        source_ids = _filter_known_ids(
            draft_scene.source_behavior_beat_ids,
            valid_sources,
            unknown_sources,
        )
        cinematic_beats = _filter_known_ids(
            [*draft_scene.cinematic_beats, draft_scene.cinematic_beat or ""],
            valid_beats,
            unknown_beats,
        )
        execution_evidence = _compile_execution_evidence(
            draft_scene.execution_actions,
            valid_moments=valid_moments,
            valid_sources=valid_sources,
            unknown_moments=unknown_moments,
            unknown_sources=unknown_sources,
            claims=claims,
        )
        phase_evidence = _compile_phase_evidence(
            draft_scene.phase_tags,
            moment_ids=moment_ids,
            source_ids=source_ids,
            valid_moments=valid_moments,
        )

        start_second, end_second = boundaries[index]
        frame_anchor = (
            "first_frame"
            if index == 0
            else "last_frame"
            if index == len(normalized_draft.scenes) - 1
            else "transition"
        )
        compiled_scenes.append(
            FrameAnchoredStoryboardScene(
                scene_index=index + 1,
                start_second=start_second,
                end_second=end_second,
                frame_anchor=frame_anchor,
                visual=draft_scene.visual,
                motion=draft_scene.motion,
                transition_goal=draft_scene.transition_goal,
                subtitle=draft_scene.subtitle,
                voiceover=draft_scene.voiceover,
                sound_effects=list(draft_scene.sound_effects),
                notes=draft_scene.notes,
                cinematic_beat=cinematic_beats[0] if cinematic_beats else None,
                cinematic_beats=cinematic_beats,
                signature_moment_ids=moment_ids,
                source_behavior_beat_ids=source_ids,
                phase_evidence=phase_evidence,
                execution_evidence=execution_evidence,
                camera_instruction=draft_scene.camera_instruction,
                tension_stage=_compile_tension_stage(
                    index=index,
                    scene_count=len(normalized_draft.scenes),
                    start_second=start_second,
                    end_second=end_second,
                    duration_seconds=float(duration_seconds),
                    frame_analysis=frame_analysis,
                    hint=draft_scene.tension_stage_hint,
                ),
                action_result_requirement=draft_scene.action_result_requirement,
                effect_timing=draft_scene.effect_timing,
                subject_motion_intensity=_clamp_intensity(
                    draft_scene.subject_motion_intensity
                ),
                camera_intensity=_clamp_intensity(draft_scene.camera_intensity),
                effect_intensity=_clamp_intensity(draft_scene.effect_intensity),
                anchor_return_instruction=draft_scene.anchor_return_instruction,
                overlay_instruction=draft_scene.overlay_instruction,
                anti_flattening_requirement=draft_scene.anti_flattening_requirement,
            )
        )

    _log_unknown_ids(
        unknown_moments=unknown_moments,
        unknown_sources=unknown_sources,
        unknown_beats=unknown_beats,
    )
    return FrameAnchoredStoryboard(
        duration_seconds=duration_seconds,
        aspect_ratio=aspect_ratio,
        scenes=compiled_scenes,
        sound_design=normalized_draft.sound_design,
        rationale=normalized_draft.rationale,
    )


def _coerce_draft(
    value: FrameAnchoredStoryboardDraft | FrameAnchoredStoryboard,
) -> FrameAnchoredStoryboardDraft:
    if isinstance(value, FrameAnchoredStoryboardDraft):
        return value

    scenes: list[dict[str, Any]] = []
    for scene in value.scenes:
        payload = scene.model_dump(mode="python")
        payload["tension_stage_hint"] = payload.pop("tension_stage", None)
        payload["phase_tags"] = list(
            dict.fromkeys(item.phase for item in scene.phase_evidence)
        )
        payload["execution_actions"] = [
            {
                "executor_kind": item.executor_kind,
                "assertion": item.assertion,
                "action_or_state_change": item.action_or_state_change,
                "signature_moment_ids": list(item.signature_moment_ids),
                "source_behavior_beat_ids": list(item.source_behavior_beat_ids),
            }
            for item in scene.execution_evidence
        ]
        payload.pop("phase_evidence", None)
        payload.pop("execution_evidence", None)
        scenes.append(payload)
    return FrameAnchoredStoryboardDraft(
        duration_seconds=value.duration_seconds,
        aspect_ratio=value.aspect_ratio,
        scenes=scenes,
        sound_design=value.sound_design,
        rationale=value.rationale,
    )


def _filter_known_ids(
    values: Iterable[str],
    allowed: object,
    unknown: set[str],
) -> list[str]:
    allowed_ids = set(allowed)
    result: list[str] = []
    for value in values:
        clean = str(value).strip()
        if not clean or clean in result:
            continue
        if clean not in allowed_ids:
            unknown.add(clean)
            continue
        result.append(clean)
    return result


def _compile_execution_evidence(
    actions: list[StoryboardDraftExecutionAction],
    *,
    valid_moments: dict[str, object],
    valid_sources: set[str],
    unknown_moments: set[str],
    unknown_sources: set[str],
    claims: dict[tuple[object, ...], str],
) -> list[StoryboardExecutionEvidence]:
    evidence: list[StoryboardExecutionEvidence] = []
    for action in actions:
        action_text = action.action_or_state_change.strip()
        moment_ids = _filter_known_ids(
            action.signature_moment_ids,
            valid_moments,
            unknown_moments,
        )
        source_ids = _filter_known_ids(
            action.source_behavior_beat_ids,
            valid_sources,
            unknown_sources,
        )
        if not action_text or not moment_ids or not source_ids:
            continue
        claim_key = (
            action.executor_kind,
            action.assertion,
            action_text,
            tuple(sorted(moment_ids)),
            tuple(sorted(source_ids)),
        )
        claim_id = claims.get(claim_key)
        if claim_id is None:
            claim_id = f"__sbv2_claim_{len(claims) + 1:03d}__"
            claims[claim_key] = claim_id
        evidence.append(
            StoryboardExecutionEvidence(
                claim_id=claim_id,
                executor_kind=action.executor_kind,
                assertion=action.assertion,
                action_or_state_change=action_text,
                signature_moment_ids=moment_ids,
                source_behavior_beat_ids=source_ids,
            )
        )
    return evidence


def _compile_phase_evidence(
    phases: Iterable[str],
    *,
    moment_ids: list[str],
    source_ids: list[str],
    valid_moments: dict[str, object],
) -> list[StoryboardPhaseEvidence]:
    evidence: list[StoryboardPhaseEvidence] = []
    unique_phases = list(
        dict.fromkeys(str(phase).strip() for phase in phases if str(phase).strip())
    )
    available_sources = set(source_ids)
    for phase in unique_phases:
        for moment_id in moment_ids:
            moment = valid_moments[moment_id]
            linked_sources = [
                source_id
                for source_id in moment.source_behavior_beat_ids
                if source_id in available_sources
            ]
            if not linked_sources:
                continue
            evidence.append(
                StoryboardPhaseEvidence(
                    phase=phase,
                    signature_moment_ids=[moment_id],
                    source_behavior_beat_ids=list(dict.fromkeys(linked_sources)),
                )
            )
    return evidence


def _compile_scene_boundaries(
    scenes: list[FrameAnchoredStoryboardDraftScene],
    *,
    duration_seconds: float,
) -> list[tuple[float, float]]:
    scene_count = len(scenes)
    if scene_count < 2:
        raise ValueError("storyboard draft requires at least two scenes")

    minimum_scene_duration = duration_seconds / scene_count / 100
    fallback_boundaries = [
        duration_seconds * index / scene_count
        for index in range(1, scene_count)
    ]
    proposed_boundaries: list[float] = []
    for index, fallback in enumerate(fallback_boundaries):
        candidates = (scenes[index].end_second, scenes[index + 1].start_second)
        candidate = next(
            (
                float(value)
                for value in candidates
                if value is not None
                and math.isfinite(float(value))
                and 0 < float(value) < duration_seconds
            ),
            fallback,
        )
        proposed_boundaries.append(candidate)

    timeline = [0.0, *proposed_boundaries, duration_seconds]
    has_dynamic_room = all(
        right - left >= minimum_scene_duration
        for left, right in zip(timeline, timeline[1:], strict=False)
    )
    if not has_dynamic_room:
        proposed_boundaries = fallback_boundaries

    boundaries = [0.0, *proposed_boundaries, duration_seconds]
    return [
        (boundaries[index], boundaries[index + 1])
        for index in range(scene_count)
    ]


def _compile_tension_stage(
    *,
    index: int,
    scene_count: int,
    start_second: float,
    end_second: float,
    duration_seconds: float,
    frame_analysis: FrameAnalysis,
    hint: str | None,
) -> DirectorTensionStage:
    if index == scene_count - 1:
        return "resolution"

    plan = frame_analysis.director_plan
    if plan is not None:
        for beat in plan.climax_beats:
            beat_start = beat.start_ratio * duration_seconds
            beat_end = beat.end_ratio * duration_seconds
            if max(start_second, beat_start) < min(end_second, beat_end):
                return "climax"

        best_phase: str | None = None
        best_overlap = 0.0
        for window in plan.action_arc_windows:
            window_start = window.start_ratio * duration_seconds
            window_end = window.end_ratio * duration_seconds
            overlap = max(0.0, min(end_second, window_end) - max(start_second, window_start))
            if overlap > best_overlap:
                best_overlap = overlap
                best_phase = window.phase
        if best_phase is not None:
            return _ACTION_PHASE_TO_TENSION[best_phase]

    if index == 0:
        return "setup"

    normalized_hint = (hint or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized_hint in _VALID_TENSION_STAGES:
        return normalized_hint  # type: ignore[return-value]
    if normalized_hint in _ACTION_PHASE_TO_TENSION:
        return _ACTION_PHASE_TO_TENSION[normalized_hint]
    return "escalation"


def _clamp_intensity(value: float | None) -> float | None:
    if value is None:
        return None
    return min(1.0, max(0.0, float(value)))


def _log_unknown_ids(
    *,
    unknown_moments: set[str],
    unknown_sources: set[str],
    unknown_beats: set[str],
) -> None:
    if not (unknown_moments or unknown_sources or unknown_beats):
        return
    logger.warning(
        "Storyboard V2 compiler filtered unknown private ids: moments=%s sources=%s beats=%s",
        sorted(unknown_moments),
        sorted(unknown_sources),
        sorted(unknown_beats),
    )
