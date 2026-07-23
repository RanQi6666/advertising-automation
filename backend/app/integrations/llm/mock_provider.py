import json
import re
from collections.abc import AsyncIterator
from typing import Any

from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.topic import ContentTopic
from backend.app.integrations.llm.language import build_target_language_context
from backend.app.schemas.ai import (
    CopyDraftCandidate,
    DirectorActionCorrection,
    DirectorBeat,
    FrameAnalysis,
    FrameAnchoredDirectorPlan,
    FrameAnchoredStoryboard,
    FrameAnchoredStoryboardDraft,
    FrameAnchoredStoryboardScene,
    FrameAnchoredStoryboardTextCandidate,
    FrameLanguageAnalysis,
    FrameTransitionBrief,
    FrameVisualFacts,
    ImageBrief,
    ReferenceVideoFrame,
    StoryboardSoundDesign,
    TopicCandidate,
    VideoStoryboardCandidate,
    VideoStoryboardScene,
)
from backend.app.services.creative_safety_prompts import (
    contains_creative_safety_risk,
    sanitize_creative_safety_text,
)


def _mock_storyboard_draft_from_storyboard(
    storyboard: FrameAnchoredStoryboard,
) -> FrameAnchoredStoryboardDraft:
    payload = storyboard.model_dump(mode="python")
    for scene in payload["scenes"]:
        scene["phase_tags"] = list(
            dict.fromkeys(item["phase"] for item in scene.pop("phase_evidence", []))
        )
        scene["execution_actions"] = [
            {key: value for key, value in item.items() if key != "claim_id"}
            for item in scene.pop("execution_evidence", [])
        ]
        scene["tension_stage_hint"] = scene.pop("tension_stage", None)
    return FrameAnchoredStoryboardDraft.model_validate(payload)


def _mock_core_behavior_beats(frame_analysis: FrameAnalysis) -> list[Any]:
    reference = frame_analysis.reference_video_analysis
    graph = reference.behavior_graph if reference is not None else None
    if graph is None:
        return []
    return [
        beat
        for beat in graph.beats
        if beat.importance == "core" and beat.behavior_type in {"action", "state"}
    ]


def _evidence_category_score(texts: list[str], categories: tuple[tuple[str, ...], ...]) -> int:
    normalized = " ".join(text.casefold() for text in texts if text and text.strip())
    return sum(any(term in normalized for term in category) for category in categories)


def _mock_allocation_evidence(
    frame_analysis: FrameAnalysis, core_beats: list[Any]
) -> dict[str, int]:
    transition = frame_analysis.transition_brief
    transition_texts = [
        transition.visual_transition,
        transition.narrative_arc,
        *transition.continuity_requirements,
    ]
    divergence = _evidence_category_score(
        transition_texts,
        (
            ("temporary", "diverge", "depart"),
            ("pose", "orientation", "turn"),
            ("screen position", "position", "translate"),
            ("composition", "framing", "shot scale", "scale"),
        ),
    )
    payoff_readability = _evidence_category_score(
        [item for beat in core_beats for item in beat.visible_evidence],
        (
            ("consequence", "result", "payoff", "resulting"),
            ("readable", "clearly", "visible after"),
            ("persist", "remain", "stable", "stabilized"),
            ("layered", "multiple", "state transition"),
        ),
    )

    first = frame_analysis.first_frame
    last = frame_analysis.last_frame
    endpoint_difference = sum(
        first_value != last_value
        for first_value, last_value in (
            (first.composition.strip().casefold(), last.composition.strip().casefold()),
            (
                first.camera_perspective.strip().casefold(),
                last.camera_perspective.strip().casefold(),
            ),
            (
                tuple(item.strip().casefold() for item in first.visible_subjects),
                tuple(item.strip().casefold() for item in last.visible_subjects),
            ),
        )
    )

    camera_travel = 0
    effect_readability = 0
    reference = frame_analysis.reference_video_analysis
    if reference is not None:
        intensity_scores = {"low": 0, "medium": 1, "high": 2}
        for segment in reference.segments:
            movement = segment.camera.movement.casefold()
            movement_score = _evidence_category_score(
                [movement],
                (
                    ("pan", "tilt", "push", "pull", "track"),
                    ("orbit", "dolly", "travel", "crane", "sweep"),
                ),
            )
            camera_travel += movement_score + intensity_scores.get(
                segment.camera.intensity.casefold(), 0
            )
            effect_readability += _evidence_category_score(
                segment.effects,
                (
                    ("layer", "multiple", "stack"),
                    ("persist", "sustain", "linger"),
                    ("readable", "reveal", "payoff", "result"),
                ),
            )

    return {
        "divergence": divergence,
        "payoff_readability": payoff_readability,
        "endpoint_difference": endpoint_difference,
        "camera_travel": camera_travel,
        "effect_readability": effect_readability,
    }


def _mock_desired_phase_durations(
    frame_analysis: FrameAnalysis,
    core_beats: list[Any],
) -> dict[str, float]:
    action_count = sum(beat.behavior_type == "action" for beat in core_beats)
    state_count = sum(beat.behavior_type == "state" for beat in core_beats)
    evidence_items = sum(len(beat.visible_evidence) for beat in core_beats)
    transition = frame_analysis.transition_brief
    endpoint_evidence = (
        len(transition.shared_visual_facts)
        + len(transition.continuity_requirements)
        + bool(transition.visual_transition.strip())
        + bool(transition.narrative_arc.strip())
    )
    allocation = _mock_allocation_evidence(frame_analysis, core_beats)
    readable_execution = sum(
        max(0.25, beat.minimum_readable_duration_seconds)
        * (1.0 if beat.behavior_type == "action" else 0.85)
        for beat in core_beats
    )
    return {
        "preparation": 0.3 + 0.08 * len(core_beats) + 0.06 * state_count,
        "action": (
            readable_execution
            + 0.12 * action_count
            + 0.10 * allocation["divergence"]
            + 0.06 * allocation["camera_travel"]
        ),
        "payoff": (
            0.3
            + 0.12 * len(core_beats)
            + 0.04 * evidence_items
            + 0.08 * allocation["payoff_readability"]
            + 0.07 * allocation["effect_readability"]
        ),
        "return": (
            0.4
            + 0.05 * endpoint_evidence
            + 0.06 * state_count
            + 0.12 * allocation["endpoint_difference"]
            + 0.03 * allocation["divergence"]
        ),
        "final_lock": 0.3 + 0.04 * len(transition.continuity_requirements),
    }


def _mock_phase_durations(
    frame_analysis: FrameAnalysis,
    core_beats: list[Any],
    duration_seconds: int,
) -> dict[str, float]:
    action_count = sum(beat.behavior_type == "action" for beat in core_beats)
    state_count = sum(beat.behavior_type == "state" for beat in core_beats)
    evidence_items = sum(len(beat.visible_evidence) for beat in core_beats)
    transition = frame_analysis.transition_brief
    endpoint_evidence = (
        len(transition.shared_visual_facts)
        + len(transition.continuity_requirements)
        + bool(transition.visual_transition.strip())
        + bool(transition.narrative_arc.strip())
    )
    allocation = _mock_allocation_evidence(frame_analysis, core_beats)
    desired = _mock_desired_phase_durations(frame_analysis, core_beats)
    minimums = {
        "preparation": 0.08 + 0.02 * state_count,
        "action": sum(
            max(0.08, beat.minimum_readable_duration_seconds * 0.45) for beat in core_beats
        ),
        "payoff": 0.08 + 0.02 * len(core_beats),
        "return": 0.08 + 0.01 * endpoint_evidence,
        "final_lock": 0.08,
    }
    budget = max(float(duration_seconds), 0.001)
    desired_total = sum(desired.values())
    minimum_total = sum(minimums.values())
    if budget < desired_total:
        if budget <= minimum_total:
            return minimums
        available_flex = budget - minimum_total
        desired_flex_total = desired_total - minimum_total
        return {
            phase: minimums[phase]
            + (desired[phase] - minimums[phase]) * available_flex / desired_flex_total
            for phase in desired
        }

    surplus = budget - desired_total
    expansion_weights = {
        "preparation": len(core_beats) + state_count + 1,
        "action": (
            action_count + state_count + allocation["divergence"] + allocation["camera_travel"]
        ),
        "payoff": (
            evidence_items
            + len(core_beats)
            + 1
            + allocation["payoff_readability"]
            + allocation["effect_readability"]
        ),
        "return": (endpoint_evidence + state_count + 1 + 2 * allocation["endpoint_difference"]),
        "final_lock": len(transition.continuity_requirements) + 1,
    }
    expansion_total = sum(expansion_weights.values())
    return {
        phase: desired[phase] + surplus * expansion_weights[phase] / expansion_total
        for phase in desired
    }


def _mock_action_arc_windows(
    frame_analysis: FrameAnalysis,
    core_beats: list[Any],
    duration_seconds: int,
) -> list[dict[str, Any]]:
    durations = _mock_phase_durations(frame_analysis, core_beats, duration_seconds)
    total = sum(durations.values())
    phases = ("preparation", "action", "payoff", "return", "final_lock")
    objectives = {
        "preparation": "Prepare readable subject/state execution from the opening anchor.",
        "action": "Execute every required target-compatible core action or state change.",
        "payoff": "Show the visible result of each executed core behavior.",
        "return": "Return continuously from the payoff to the supplied ending anchor.",
        "final_lock": "Hold the exact supplied final anchor without a cut.",
    }
    intensities = {
        "preparation": (0.3, 0.2, 0.1),
        "action": (0.9, 0.65, 0.45),
        "payoff": (0.55, 0.5, 0.8),
        "return": (0.45, 0.35, 0.2),
        "final_lock": (0.05, 0.05, 0.0),
    }
    windows: list[dict[str, Any]] = []
    elapsed = 0.0
    previous_id: str | None = None
    for index, phase in enumerate(phases):
        start_ratio = elapsed / total
        elapsed += durations[phase]
        end_ratio = 1.0 if index == len(phases) - 1 else elapsed / total
        window_id = f"{phase}_window"
        subject, camera, effect = intensities[phase]
        windows.append(
            {
                "window_id": window_id,
                "phase": phase,
                "start_ratio": round(start_ratio, 6),
                "end_ratio": round(end_ratio, 6),
                "objective": objectives[phase],
                "subject_motion_intensity": subject,
                "camera_intensity": camera,
                "effect_intensity": effect,
                "depends_on": [previous_id] if previous_id else [],
            }
        )
        previous_id = window_id
    return windows


def _mock_phase_boundary(
    plan: FrameAnchoredDirectorPlan,
    phase: str,
    duration_seconds: int,
    *,
    use_start: bool = False,
) -> float:
    window = next(window for window in plan.action_arc_windows if window.phase == phase)
    ratio = window.start_ratio if use_start else window.end_ratio
    return round(duration_seconds * ratio, 6)


def _mock_requires_phase_compression(
    frame_analysis: FrameAnalysis,
    director_plan: FrameAnchoredDirectorPlan,
    source_ids: set[str],
    duration_seconds: int,
) -> bool:
    from backend.app.services.storyboard_director_coverage_service import (
        _phase_compression_required_for_duration,
    )

    return _phase_compression_required_for_duration(
        float(duration_seconds), source_ids, frame_analysis, director_plan
    )


class MockLLMProvider:
    """Deterministic provider for local development and tests."""

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
        del (
            duration_seconds,
            aspect_ratio,
            reference_frames,
            reference_video_duration_seconds,
            reference_video_sample_interval_seconds,
        )
        return FrameAnalysis(
            first_frame=FrameVisualFacts(
                visible_subjects=[f"Opening frame from {first_frame_image_url}"],
                environment="Opening-frame environment",
                composition="Opening-frame composition",
                camera_perspective="Opening-frame perspective",
                visual_style="Observed visual style",
                color_and_lighting="Observed opening lighting",
                opening_state="Exact visual state of the supplied first frame.",
            ),
            last_frame=FrameVisualFacts(
                visible_subjects=[f"Ending frame from {last_frame_image_url}"],
                environment="Ending-frame environment",
                composition="Ending-frame composition",
                camera_perspective="Ending-frame perspective",
                visual_style="Observed visual style",
                color_and_lighting="Observed ending lighting",
                ending_state="Exact visual state of the supplied last frame.",
            ),
            transition_brief=FrameTransitionBrief(
                shared_visual_facts=["Both supplied frames belong to one video arc."],
                continuity_requirements=["Preserve the supplied opening and ending states."],
                visual_transition="Use camera movement and action to bridge the frames.",
                narrative_arc="Begin at the first frame and arrive at the last frame.",
            ),
            language_analysis=FrameLanguageAnalysis(
                recommended_output_language="en",
                reason="Mock provider uses English for deterministic local output.",
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
        del first_frame_image_url, last_frame_image_url, aspect_ratio
        evidence = frame_analysis.transition_brief.visual_transition
        core_beats = _mock_core_behavior_beats(frame_analysis)
        action_arc_windows = (
            _mock_action_arc_windows(frame_analysis, core_beats, duration_seconds)
            if core_beats
            else []
        )
        action_window = next(
            (window for window in action_arc_windows if window["phase"] == "action"),
            None,
        )
        action_start = action_window["start_ratio"] if action_window else 0.4
        action_end = action_window["end_ratio"] if action_window else 0.72

        climax_beats: list[DirectorBeat] = []
        signature_moment_plan: list[dict[str, Any]] = []
        if core_beats:
            readable_total = sum(
                max(0.25, beat.minimum_readable_duration_seconds) for beat in core_beats
            )
            cursor = action_start
            action_span = action_end - action_start
            for index, behavior in enumerate(core_beats, start=1):
                source_evidence = list(behavior.visible_evidence) or [behavior.description]
                assigned_beat_id = f"mock_core_peak_{index}"
                behavior_share = (
                    max(0.25, behavior.minimum_readable_duration_seconds) / readable_total
                )
                beat_end = (
                    action_end
                    if index == len(core_beats)
                    else cursor + action_span * behavior_share
                )
                climax_beats.append(
                    DirectorBeat(
                        beat_id=assigned_beat_id,
                        stage="climax",
                        source_evidence=source_evidence,
                        start_ratio=round(cursor, 6),
                        end_ratio=round(beat_end, 6),
                        attention_objective=(
                            f"Keep core {behavior.behavior_type} behavior {index} readable."
                        ),
                        camera_instruction=(
                            "Support execution without replacing subject/state motion."
                        ),
                        action_requirement=(
                            f"Execute core {behavior.behavior_type} behavior {index} "
                            "before its result."
                        ),
                        effect_requirement="Peak support only after the behavior reads.",
                        importance="core",
                    )
                )
                signature_moment_plan.append(
                    {
                        "moment_id": f"signature_core_{index}",
                        "moment_type": "combined",
                        "source_evidence": source_evidence,
                        "source_behavior_beat_ids": [behavior.beat_id],
                        "transfer_role": "primary_action",
                        "strategy": "adapt",
                        "target_adaptation": (
                            f"Adapt core {behavior.behavior_type} behavior {index} to target facts."
                        ),
                        "adapted_action": (
                            f"The target subject or state performs the readable core "
                            f"{behavior.behavior_type} change {index}."
                        ),
                        "temporary_divergence": (
                            "Allow a distinct middle pose or state before the continuous return."
                        ),
                        "camera_support": "Reframe continuously around execution and payoff.",
                        "effect_support": "Support the consequence only after motion reads.",
                        "visible_payoff": (
                            "Show the visible target-compatible result after the linked "
                            "behavior reads."
                        ),
                        "return_strategy": (
                            "Return continuously from the payoff into the exact final anchor."
                        ),
                        "assigned_beat_id": assigned_beat_id,
                    }
                )
                cursor = beat_end
        else:
            climax_beats = [
                DirectorBeat(
                    beat_id="mock_causal_peak",
                    stage="climax",
                    source_evidence=[evidence],
                    start_ratio=0.4,
                    end_ratio=0.72,
                    attention_objective="Focus attention on the visible transition.",
                    camera_instruction="Use an in-shot camera change to emphasize the peak.",
                    action_requirement="Maintain a continuous transition between target frames.",
                    effect_requirement="Support only evidence-backed visual changes.",
                    importance="core",
                )
            ]

        return FrameAnchoredDirectorPlan(
            narrative_objective=(
                "Turn the supplied opening into the supplied ending through readable "
                "evidence-backed behavior."
            ),
            attention_path=[
                "opening state",
                "preparation",
                "core behavior",
                "visible result",
                "ending state",
            ],
            tension_curve=["setup", "trigger", "escalation", "climax", "resolution"],
            climax_beats=climax_beats,
            action_arc_windows=action_arc_windows,
            signature_moment_plan=signature_moment_plan,
            final_anchor_return=(
                "Return continuously from every payoff into the exact supplied final anchor."
                if core_beats
                else ""
            ),
            anchor_adaptation_plan=[
                "Begin from the supplied first frame and resolve to the supplied last frame."
            ],
            anti_flattening_constraints=[
                "Keep preparation, subject/state execution, payoff, return, and final hold "
                "readable."
            ],
        )

    async def generate_frame_anchored_video_storyboard_text(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        frame_analysis: FrameAnalysis,
        duration_seconds: int,
        aspect_ratio: str,
    ) -> FrameAnchoredStoryboardTextCandidate:
        del first_frame_image_url, last_frame_image_url
        reference_direction = (
            "Use the analyzed reference behavior, camera rhythm, causal payoff, and VFX as "
            "target-compatible director material. "
            if frame_analysis.reference_video_analysis is not None
            else "Use the analyzed transition between the supplied endpoint images. "
        )
        return FrameAnchoredStoryboardTextCandidate(
            storyboard_text=(
                f"Create one continuous {duration_seconds}-second {aspect_ratio} cinematic clip. "
                "Begin exactly from the supplied first frame and preserve its visible target "
                "identity and composition. "
                + reference_direction
                + "Build a readable action, consequence, camera escalation, and layered effects "
                "inside the shot, then resolve coherently to the supplied last frame as the final "
                "visual anchor. Keep visible text, rewards, UI, brand, product, character, and "
                "setting consistent with the target frames and apply all safety requirements."
            )
        )

    async def generate_frame_anchored_video_storyboard(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        frame_analysis: FrameAnalysis,
        duration_seconds: int,
        aspect_ratio: str,
        director_corrections: list[DirectorActionCorrection] | None = None,
    ) -> FrameAnchoredStoryboardDraft:
        del first_frame_image_url, last_frame_image_url
        director_plan = frame_analysis.director_plan
        signature_moments = [
            moment
            for moment in (director_plan.signature_moment_plan if director_plan else [])
            if moment.strategy != "omit"
        ]
        beats_by_id = {
            beat.beat_id: beat for beat in (director_plan.climax_beats if director_plan else [])
        }
        assigned_beats = [
            beats_by_id[moment.assigned_beat_id]
            for moment in signature_moments
            if moment.assigned_beat_id in beats_by_id
        ]
        signature_id_set = {moment.moment_id for moment in signature_moments}
        source_id_set = {
            source_id
            for moment in signature_moments
            for source_id in moment.source_behavior_beat_ids
        }
        applicable_corrections = [
            correction
            for correction in (director_corrections or [])
            if signature_id_set.intersection(correction.signature_moment_ids)
            and source_id_set.intersection(correction.source_behavior_beat_ids)
        ]
        correction_types = {correction.correction_type for correction in applicable_corrections}

        if signature_moments and assigned_beats and director_plan is not None:
            signature_ids = [moment.moment_id for moment in signature_moments]
            source_ids = list(
                dict.fromkeys(
                    source_id
                    for moment in signature_moments
                    for source_id in moment.source_behavior_beat_ids
                )
            )
            beat_ids = list(
                dict.fromkeys(
                    moment.assigned_beat_id
                    for moment in signature_moments
                    if moment.assigned_beat_id
                )
            )
            motion = " ".join(moment.adapted_action for moment in signature_moments)
            if "execution" in correction_types:
                motion = (
                    f"{motion} Execute the linked subject/state action as independent readable "
                    "motion."
                ).strip()
            payoff = " ".join(moment.visible_payoff for moment in signature_moments)
            if correction_types.intersection({"execution", "payoff"}):
                payoff = (
                    f"{payoff} Make the linked visible consequence readable after execution."
                ).strip()
            camera_instruction = " ".join(
                dict.fromkeys(
                    moment.camera_support or beats_by_id[moment.assigned_beat_id].camera_instruction
                    for moment in signature_moments
                    if moment.assigned_beat_id in beats_by_id
                )
            )
            effect_timing = " ".join(
                dict.fromkeys(
                    moment.effect_support or beats_by_id[moment.assigned_beat_id].effect_requirement
                    for moment in signature_moments
                    if moment.assigned_beat_id in beats_by_id
                )
            )
            return_instruction = (
                " ".join(
                    dict.fromkeys(
                        moment.return_strategy
                        for moment in signature_moments
                        if moment.return_strategy
                    )
                )
                or director_plan.final_anchor_return
            )
            if "return" in correction_types:
                return_instruction = (
                    f"{return_instruction} Return the linked result continuously before the "
                    "final hold."
                ).strip()
            preparation_end = _mock_phase_boundary(director_plan, "preparation", duration_seconds)
            action_end = _mock_phase_boundary(director_plan, "action", duration_seconds)
            payoff_end = _mock_phase_boundary(director_plan, "payoff", duration_seconds)
            final_lock_start = _mock_phase_boundary(
                director_plan, "final_lock", duration_seconds, use_start=True
            )
            behavior_types = {
                beat.beat_id: beat.behavior_type
                for beat in _mock_core_behavior_beats(frame_analysis)
            }
            execution_evidence = [
                {
                    "claim_id": f"claim:{moment.moment_id}",
                    "executor_kind": (
                        "target_state"
                        if any(
                            behavior_types.get(source_id) == "state"
                            for source_id in moment.source_behavior_beat_ids
                        )
                        else "target_subject"
                    ),
                    "assertion": "affirmed",
                    "action_or_state_change": moment.adapted_action,
                    "signature_moment_ids": [moment.moment_id],
                    "source_behavior_beat_ids": list(moment.source_behavior_beat_ids),
                }
                for moment in signature_moments
            ]

            def phase_evidence(*phases: str) -> list[dict[str, object]]:
                return [
                    {
                        "phase": phase,
                        "signature_moment_ids": [moment.moment_id],
                        "source_behavior_beat_ids": list(moment.source_behavior_beat_ids),
                    }
                    for phase in phases
                    for moment in signature_moments
                ]

            common_execution = {
                "motion": motion,
                "cinematic_beat": beat_ids[0],
                "cinematic_beats": beat_ids,
                "signature_moment_ids": signature_ids,
                "source_behavior_beat_ids": source_ids,
                "execution_evidence": execution_evidence,
                "camera_instruction": camera_instruction,
                "tension_stage": "climax",
                "action_result_requirement": payoff,
                "effect_timing": effect_timing,
                "subject_motion_intensity": 0.9,
                "camera_intensity": 0.65,
                "effect_intensity": 0.8,
                "anti_flattening_requirement": (
                    "Keep preparation, subject/state execution, payoff, return, and final hold "
                    "distinct and readable."
                ),
                "notes": None,
            }

            compression_required = _mock_requires_phase_compression(
                frame_analysis, director_plan, set(source_ids), duration_seconds
            )
            if not compression_required:
                action_fields = dict(common_execution)
                action_fields["action_result_requirement"] = None
                action_fields["effect_timing"] = (
                    "Keep support subordinate until the linked subject/state execution reads."
                )
                action_fields["effect_intensity"] = 0.45
                scenes = [
                    FrameAnchoredStoryboardScene(
                        scene_index=1,
                        start_second=0,
                        end_second=preparation_end,
                        frame_anchor="first_frame",
                        visual="Hold the opening anchor while preparing every linked behavior.",
                        motion="Prepare each linked subject/state action before execution.",
                        transition_goal="Preparation leads continuously into linked execution.",
                        sound_effects=["Soft preparation ambience."],
                        signature_moment_ids=signature_ids,
                        source_behavior_beat_ids=source_ids,
                        phase_evidence=phase_evidence("preparation"),
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=2,
                        start_second=preparation_end,
                        end_second=action_end,
                        frame_anchor="transition",
                        visual=(
                            "Execute every linked core behavior as readable subject/state motion."
                        ),
                        transition_goal=(
                            "Allow temporary divergence while every linked behavior executes."
                        ),
                        sound_effects=["Execution rise."],
                        phase_evidence=phase_evidence("action"),
                        **action_fields,
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=3,
                        start_second=action_end,
                        end_second=payoff_end,
                        frame_anchor="transition",
                        visual="Show every linked visible consequence after its execution.",
                        motion="The linked consequences become readable before the return begins.",
                        transition_goal="Hold the results long enough to read without a cut.",
                        sound_effects=["Payoff accent."],
                        signature_moment_ids=signature_ids,
                        source_behavior_beat_ids=source_ids,
                        camera_instruction=camera_instruction,
                        tension_stage="resolution",
                        action_result_requirement=payoff,
                        effect_timing=effect_timing,
                        subject_motion_intensity=0.55,
                        camera_intensity=0.5,
                        effect_intensity=0.8,
                        phase_evidence=phase_evidence("payoff"),
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=4,
                        start_second=payoff_end,
                        end_second=final_lock_start,
                        frame_anchor="transition",
                        visual="Return continuously from the payoff to the ending composition.",
                        motion=(
                            "Settle subject/state motion toward the exact supplied ending state."
                        ),
                        transition_goal="Restore ending pose, framing, and camera in-shot.",
                        sound_effects=["Return sweep."],
                        signature_moment_ids=signature_ids,
                        source_behavior_beat_ids=source_ids,
                        anchor_return_instruction=return_instruction,
                        phase_evidence=phase_evidence("return"),
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=5,
                        start_second=final_lock_start,
                        end_second=duration_seconds,
                        frame_anchor="last_frame",
                        visual="Hold and lock the exact supplied last-frame visual state.",
                        motion="Stabilize and hold the supplied final composition.",
                        transition_goal="Maintain the final hold without an end card or cut.",
                        sound_effects=["Ending ambience."],
                        signature_moment_ids=signature_ids,
                        source_behavior_beat_ids=source_ids,
                        phase_evidence=phase_evidence("final_hold"),
                    ),
                ]
            elif duration_seconds >= 4:
                scenes = [
                    FrameAnchoredStoryboardScene(
                        scene_index=1,
                        start_second=0,
                        end_second=preparation_end,
                        frame_anchor="first_frame",
                        visual=(
                            "Hold the supplied opening anchor while preparing the core behavior."
                        ),
                        motion=(
                            "Prepare the subject/state and begin departing from the opening hold."
                        ),
                        transition_goal="Preparation leads continuously into execution.",
                        sound_effects=["Soft preparation ambience."],
                        signature_moment_ids=signature_ids,
                        source_behavior_beat_ids=source_ids,
                        phase_evidence=phase_evidence("preparation"),
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=2,
                        start_second=preparation_end,
                        end_second=payoff_end,
                        frame_anchor="transition",
                        visual="Execute every linked core behavior and show each visible payoff.",
                        transition_goal=(
                            "Allow temporary divergence, execute all linked behavior, and show "
                            "the payoff without a cut."
                        ),
                        sound_effects=["Execution rise.", "Payoff accent."],
                        phase_evidence=phase_evidence("action", "payoff"),
                        **common_execution,
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=3,
                        start_second=payoff_end,
                        end_second=final_lock_start,
                        frame_anchor="transition",
                        visual="Return continuously from the payoff to the ending composition.",
                        motion=(
                            "Settle subject/state motion toward the exact supplied ending state."
                        ),
                        transition_goal="Restore ending pose, framing, and camera in-shot.",
                        sound_effects=["Return sweep."],
                        signature_moment_ids=signature_ids,
                        source_behavior_beat_ids=source_ids,
                        anchor_return_instruction=return_instruction,
                        phase_evidence=phase_evidence("return"),
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=4,
                        start_second=final_lock_start,
                        end_second=duration_seconds,
                        frame_anchor="last_frame",
                        visual="Hold and lock the exact supplied last-frame visual state.",
                        motion="Stabilize and hold the supplied final composition.",
                        transition_goal="Maintain the final hold without an end card or cut.",
                        sound_effects=["Ending ambience."],
                        signature_moment_ids=signature_ids,
                        source_behavior_beat_ids=source_ids,
                        phase_evidence=phase_evidence("final_hold"),
                    ),
                ]
            elif duration_seconds == 3:
                scenes = [
                    FrameAnchoredStoryboardScene(
                        scene_index=1,
                        start_second=0,
                        end_second=preparation_end,
                        frame_anchor="first_frame",
                        visual="Prepare from the exact supplied opening anchor.",
                        motion="Prepare subject/state motion and depart from the opening hold.",
                        transition_goal="Preparation leads directly into execution.",
                        sound_effects=["Soft preparation ambience."],
                        signature_moment_ids=signature_ids,
                        source_behavior_beat_ids=source_ids,
                        phase_evidence=phase_evidence("preparation"),
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=2,
                        start_second=preparation_end,
                        end_second=final_lock_start,
                        frame_anchor="transition",
                        visual="Execute all linked core behaviors and show their payoff.",
                        transition_goal=(
                            "Share execution and payoff with temporary divergence in one readable "
                            "continuous scene."
                        ),
                        sound_effects=["Execution rise.", "Payoff accent."],
                        phase_evidence=phase_evidence("action", "payoff"),
                        **common_execution,
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=3,
                        start_second=final_lock_start,
                        end_second=duration_seconds,
                        frame_anchor="last_frame",
                        visual="Return, settle, hold, and lock the exact supplied last frame.",
                        motion="Return continuously, stabilize, and hold the final composition.",
                        transition_goal="Share return and final hold without a cut.",
                        sound_effects=["Return sweep.", "Ending ambience."],
                        signature_moment_ids=signature_ids,
                        source_behavior_beat_ids=source_ids,
                        anchor_return_instruction=return_instruction,
                        phase_evidence=phase_evidence("return", "final_hold"),
                    ),
                ]
            else:
                scenes = [
                    FrameAnchoredStoryboardScene(
                        scene_index=1,
                        start_second=0,
                        end_second=preparation_end,
                        frame_anchor="first_frame",
                        visual="Prepare from the exact supplied opening anchor.",
                        motion="Prepare subject/state motion and depart from the opening hold.",
                        transition_goal="Preparation leads directly into shared execution.",
                        sound_effects=["Soft preparation ambience."],
                        signature_moment_ids=signature_ids,
                        source_behavior_beat_ids=source_ids,
                        phase_evidence=phase_evidence("preparation"),
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=2,
                        start_second=preparation_end,
                        end_second=duration_seconds,
                        frame_anchor="last_frame",
                        visual=(
                            "Temporarily diverge, execute every linked core behavior, show each "
                            "payoff, return, settle, and hold the exact final anchor."
                        ),
                        transition_goal=(
                            "Share execution, payoff, temporary divergence, return, and final hold "
                            "in one continuous scene."
                        ),
                        sound_effects=["Execution rise.", "Payoff accent.", "Ending ambience."],
                        anchor_return_instruction=return_instruction,
                        phase_evidence=phase_evidence("action", "payoff", "return", "final_hold"),
                        **common_execution,
                    ),
                ]
            rationale = (
                "Mock storyboard deterministically allocates evidence-driven preparation, "
                "execution, payoff, return, and final hold in one continuous shot."
            )
        else:
            transition = frame_analysis.transition_brief
            preparation_weight = 0.35 + 0.05 * len(transition.shared_visual_facts)
            bridge_weight = 0.7 + 0.08 * bool(transition.visual_transition.strip())
            final_weight = 0.4 + 0.05 * len(transition.continuity_requirements)
            total_weight = preparation_weight + bridge_weight + final_weight
            preparation_end = round(duration_seconds * preparation_weight / total_weight, 6)
            final_start = round(
                duration_seconds * (preparation_weight + bridge_weight) / total_weight, 6
            )
            if duration_seconds >= 3:
                scenes = [
                    FrameAnchoredStoryboardScene(
                        scene_index=1,
                        start_second=0,
                        end_second=preparation_end,
                        frame_anchor="first_frame",
                        visual="Begin from the exact supplied first-frame visual state.",
                        motion="Prepare a subtle continuous transition.",
                        transition_goal="Depart without inventing unsupported core behavior.",
                        sound_effects=["Soft opening ambience."],
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=2,
                        start_second=preparation_end,
                        end_second=final_start,
                        frame_anchor="transition",
                        visual="Bridge the supplied visual states continuously.",
                        motion="Camera and observed state continuity progress toward the ending.",
                        transition_goal="Maintain evidence-backed visual continuity.",
                        sound_effects=["Transition ambience."],
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=3,
                        start_second=final_start,
                        end_second=duration_seconds,
                        frame_anchor="last_frame",
                        visual="Settle, hold, and lock the exact supplied last-frame state.",
                        motion="Stabilize and hold the final composition.",
                        transition_goal="Preserve the ending frame without an end card.",
                        sound_effects=["Ending ambience."],
                    ),
                ]
            else:
                scenes = [
                    FrameAnchoredStoryboardScene(
                        scene_index=1,
                        start_second=0,
                        end_second=preparation_end,
                        frame_anchor="first_frame",
                        visual="Begin from and prepare to leave the exact first frame.",
                        motion="Prepare the continuous transition.",
                        transition_goal="Depart without inventing unsupported behavior.",
                        sound_effects=["Soft opening ambience."],
                    ),
                    FrameAnchoredStoryboardScene(
                        scene_index=2,
                        start_second=preparation_end,
                        end_second=duration_seconds,
                        frame_anchor="last_frame",
                        visual="Bridge, settle, hold, and lock the exact supplied last frame.",
                        motion="Complete the transition and stabilize the final composition.",
                        transition_goal="Share bridge and final hold without a cut.",
                        sound_effects=["Transition ambience.", "Ending ambience."],
                    ),
                ]
            rationale = "Mock storyboard bridges only the supplied target-frame evidence."

        storyboard = FrameAnchoredStoryboard(
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            scenes=scenes,
            sound_design=StoryboardSoundDesign(
                music="A restrained cinematic pulse that resolves at the final hold.",
                ambience="Continuous environment bed that preserves one-shot continuity.",
            ),
            rationale=rationale,
        )
        return _mock_storyboard_draft_from_storyboard(storyboard)

    async def extract_delivery_fields(self, raw_content: str) -> dict:
        url_candidates = _find_urls(raw_content)
        country_candidates = _find_country_candidates(raw_content)
        event_value, event_status, event_reason = _find_event(raw_content)
        age_min, age_max = _find_age_range(raw_content)
        gender_value = _find_gender(raw_content)
        audience_raw = _find_audience_raw(raw_content, age_min, age_max, gender_value)

        return {
            "schema_version": "ad_delivery_extract_v1",
            "fields": {
                "landing_url": _field(
                    value=url_candidates[0] if len(url_candidates) == 1 else None,
                    status=(
                        "extracted"
                        if len(url_candidates) == 1
                        else "conflict"
                        if len(url_candidates) > 1
                        else "missing"
                    ),
                    confidence=0.96 if len(url_candidates) == 1 else 0.6 if url_candidates else 0,
                    evidence=url_candidates[:3],
                    candidates=url_candidates,
                    reason=(
                        "识别到唯一链接。"
                        if len(url_candidates) == 1
                        else "识别到多个链接，请人工选择。"
                        if len(url_candidates) > 1
                        else "未识别到落地页链接。"
                    ),
                ),
                "event_name": _field(
                    value=event_value,
                    normalized_value=_normalize_event(event_value),
                    status=event_status,
                    confidence=0.88 if event_status == "extracted" else 0.55,
                    evidence=[event_reason] if event_reason else [],
                    candidates=[event_value] if event_value else ["流量"],
                    reason=event_reason or "未识别到投放事件，默认建议流量。",
                ),
                "country": _field(
                    value=country_candidates[0] if len(country_candidates) == 1 else None,
                    normalized_value=(
                        _normalize_country(country_candidates[0])
                        if len(country_candidates) == 1
                        else None
                    ),
                    status=(
                        "extracted"
                        if len(country_candidates) == 1
                        else "conflict"
                        if len(country_candidates) > 1
                        else "missing"
                    ),
                    confidence=0.9 if len(country_candidates) == 1 else 0.55,
                    evidence=country_candidates[:3],
                    candidates=country_candidates,
                    reason=(
                        "识别到唯一投放国家。"
                        if len(country_candidates) == 1
                        else "识别到多个可能国家，请人工确认。"
                        if len(country_candidates) > 1
                        else "未识别到投放国家。"
                    ),
                ),
                "age_min": _field(
                    value=age_min,
                    normalized_value=age_min,
                    status="extracted" if age_min else "suggested",
                    confidence=0.9 if age_min else 0.45,
                    evidence=[f"{age_min}-{age_max}"] if age_min and age_max else [],
                    candidates=[age_min] if age_min else ["不限"],
                    reason="识别到年龄范围。" if age_min else "未写年龄，建议不限。",
                ),
                "age_max": _field(
                    value=age_max,
                    normalized_value=age_max,
                    status="extracted" if age_max else "suggested",
                    confidence=0.9 if age_max else 0.45,
                    evidence=[f"{age_min}-{age_max}"] if age_min and age_max else [],
                    candidates=[age_max] if age_max else ["不限"],
                    reason="识别到年龄范围。" if age_max else "未写年龄，建议不限。",
                ),
                "gender": _field(
                    value=gender_value or "不限",
                    normalized_value=_normalize_gender(gender_value),
                    status="extracted" if gender_value else "suggested",
                    confidence=0.9 if gender_value else 0.45,
                    evidence=[gender_value] if gender_value else [],
                    candidates=[gender_value] if gender_value else ["不限"],
                    reason="识别到性别定向。" if gender_value else "未写性别，建议不限。",
                ),
                "audience_description_raw": _field(
                    value=audience_raw,
                    normalized_value=audience_raw,
                    status="extracted" if audience_raw else "suggested",
                    confidence=0.85 if audience_raw else 0.45,
                    evidence=[audience_raw] if audience_raw else [],
                    candidates=[audience_raw] if audience_raw else [],
                    reason="保留人群原文供人工确认。" if audience_raw else "未识别到人群原文。",
                ),
            },
            "review": {
                "must_confirm": ["landing_url", "event_name", "country"],
                "missing_fields": [
                    key
                    for key, value in {
                        "landing_url": bool(url_candidates),
                        "country": bool(country_candidates),
                    }.items()
                    if not value
                ],
                "conflict_fields": [
                    key
                    for key, values in {
                        "landing_url": url_candidates,
                        "country": country_candidates,
                    }.items()
                    if len(values) > 1
                ],
                "suggested_fields": [
                    key
                    for key, is_suggested in {
                        "event_name": event_status == "suggested",
                        "age_min": age_min is None,
                        "age_max": age_max is None,
                        "gender": gender_value is None,
                    }.items()
                    if is_suggested
                ],
            },
        }

    async def analyze_ad_performance(self, context: dict) -> dict[str, Any]:
        if _uses_facebook_operator_result(context):
            return _mock_facebook_operator_result(context)

        metrics = context.get("metrics") if isinstance(context.get("metrics"), dict) else {}
        rule_analysis = (
            context.get("rule_analysis") if isinstance(context.get("rule_analysis"), dict) else {}
        )
        creative = context.get("creative") if isinstance(context.get("creative"), dict) else {}
        image_url = (
            creative.get("image_url")
            or creative.get("imageUrl")
            or creative.get("asset_image_url")
            or creative.get("thumbnail_url")
        )
        video_url = (
            creative.get("video_url")
            or creative.get("videoUrl")
            or creative.get("asset_video_url")
            or creative.get("source_video_url")
        )
        creative_name = creative.get("name") or creative.get("ad_name") or "该广告"
        problems = (
            rule_analysis.get("problems") if isinstance(rule_analysis.get("problems"), list) else []
        )
        problem_titles = [
            item.get("title")
            for item in problems
            if isinstance(item, dict) and isinstance(item.get("title"), str)
        ]
        root_causes = problem_titles[:4] or ["当前数据没有触发明显异常，建议继续观察核心指标。"]
        impressions = metrics.get("impressions")
        spend = metrics.get("spend")
        ctr = metrics.get("ctr")
        landing_rate = metrics.get("landing_page_view_rate")

        return {
            "summary": (
                f"{creative_name} 的 AI 分析已基于投放指标生成。"
                f"当前曝光 {impressions or '-'}，花费 {spend or '-'}，CTR {ctr or '-'}，"
                f"落地页到达率 {landing_rate or '-'}。"
            ),
            "root_causes": root_causes,
            "recommended_actions": [
                "先确认样本量、投放状态、目标事件和落地页链路，再判断素材本身是否需要重做。",
                "把标题、文案、图片或视频拆成 2-3 个差异明显的方向做小预算测试。",
                "让外部系统继续回传 purchase、add_to_cart、lead 等转化数据，以便判断真实业务效果。",
            ],
            "next_tests": [
                "保留当前广告组设置，仅替换首屏素材或前三秒钩子做 A/B 测试。",
                "保留素材不变，单独测试更贴近业务目标的优化事件。",
            ],
            "creative_feedback": [
                "如果 CTR 偏低，优先检查首屏视觉、标题利益点和前三秒表达。",
                "如果 CTR 不低但后续转化差，暂时不要急着否定素材。",
            ],
            "audience_feedback": [
                "当前人群需要结合国家、年龄、兴趣和版位继续拆分观察。",
            ],
            "landing_page_feedback": [
                "点击表现好但落地页浏览低时，优先检查页面速度、跳转链路和像素事件。",
            ],
            "budget_delivery_feedback": [
                "低曝光或零花费时，只能先判断投放是否开始跑量，不能判断素材好坏。",
            ],
            "risk_notes": [
                "样本过小时，大模型结论只能作为排查方向，不能作为最终优化依据。",
            ],
            "visual_analysis": (
                {
                    "summary": "Mock 已识别到图片素材 URL，真实 provider 会直接读取图片画面。",
                    "observed_elements": ["图片广告素材", "落地页链接", "广告正文"],
                    "strengths": ["素材 URL 已传入，可进行画面层面的判断。"],
                    "weaknesses": ["Mock 环境不会真正识图，只验证数据链路。"],
                    "recommendations": [
                        "真实环境使用支持视觉输入的模型后再判断构图、产品露出和首屏吸引力。"
                    ],
                    "risk_notes": ["如果 image_url 无法公网访问，真实模型也无法看到图片。"],
                    "source_image_url": str(image_url),
                    "source_video_url": None,
                    "confidence_note": "Mock visual analysis",
                }
                if image_url
                else {
                    "summary": "Mock 已识别到视频素材 URL，真实 provider 会直接读取视频内容。",
                    "observed_elements": ["视频广告素材", "广告正文"],
                    "strengths": ["视频 URL 已传入，可进行视频画面层面的判断。"],
                    "weaknesses": ["Mock 环境不会真正看视频，只验证数据链路。"],
                    "recommendations": [
                        "真实环境使用支持视频输入的模型后再判断前三秒钩子、节奏和素材匹配度。"
                    ],
                    "risk_notes": ["如果 video_url 无法公网访问，真实模型也无法看到视频。"],
                    "source_image_url": None,
                    "source_video_url": str(video_url),
                    "confidence_note": "Mock video analysis",
                }
                if video_url
                else None
            ),
            "optimization_work_order": _mock_ad_performance_optimization_work_order(
                creative_name=creative_name,
                creative=creative,
                metrics=metrics,
            ),
            "confidence_note": "Mock 分析用于本地开发；真实环境会调用配置的大模型 provider。",
        }

    async def stream_ad_performance_analysis(self, context: dict) -> AsyncIterator[dict[str, Any]]:
        analysis = await self.analyze_ad_performance(context)
        text = json.dumps(analysis, ensure_ascii=False)
        for chunk in _chunk_text(text, size=36):
            yield {"type": "delta", "text": chunk}
        yield {"type": "done", "analysis": analysis, "text": text}

    async def generate_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> list[TopicCandidate]:
        product = campaign.product_name or campaign.name
        audience = campaign.audience_description or "the target audience"
        work_order = signals.get("work_order") or campaign.metadata_json.get("work_order") or {}
        landing_page = (
            signals.get("landing_page") or campaign.metadata_json.get("landing_page") or {}
        )
        parsed_fields = work_order.get("parsed_fields", {})
        country = work_order.get("country") or parsed_fields.get("country")
        event_name = parsed_fields.get("event_name") or campaign.objective
        landing_title = landing_page.get("title")
        revision_feedback = _text_signal(signals, "topic_revision_feedback")
        creative_strategy = signals.get("creative_strategy") if isinstance(signals, dict) else {}
        angle_plan = (
            creative_strategy.get("topic_angle_plan")
            if isinstance(creative_strategy, dict)
            else None
        )
        angle_items = (
            [item for item in angle_plan if isinstance(item, dict)]
            if isinstance(angle_plan, list)
            else []
        )
        target_language = build_target_language_context(campaign=campaign, signals=signals)
        candidates: list[TopicCandidate] = []

        templates = _topic_templates(revision_feedback, target_language)

        for index, (title, angle) in enumerate(templates[:limit], start=1):
            candidates.append(
                TopicCandidate(
                    title=(
                        f"{product}: {title}" if target_language["country_code"] != "IN" else title
                    ),
                    angle=_append_context(
                        (
                            f"{angle_items[index - 1].get('angle_type')}: "
                            f"{angle_items[index - 1].get('purpose')}"
                        )
                        if index - 1 < len(angle_items)
                        else angle,
                        country=country,
                        event_name=event_name,
                        landing_title=landing_title,
                    ),
                    angle_type=(
                        str(angle_items[index - 1].get("angle_type"))
                        if index - 1 < len(angle_items)
                        else None
                    ),
                    audience=audience,
                    selling_points=[
                        f"Clear benefit for {product}",
                        "Simple Facebook-friendly message",
                        "Can be adapted into multiple creative images",
                    ],
                    risk_notes="Validate product claims and avoid unsupported guarantees.",
                    rationale=(
                        f"Mock topic {index} generated from campaign and work order signals."
                        + (
                            f" Operator feedback applied: {revision_feedback}."
                            if revision_feedback
                            else ""
                        )
                    ),
                    score=max(0.1, 0.95 - index * 0.06),
                )
            )
        return candidates

    async def stream_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> AsyncIterator[TopicCandidate]:
        topics = await self.generate_topics(campaign=campaign, limit=limit, signals=signals)
        for topic in topics:
            yield topic

    async def generate_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        constraints: dict,
    ) -> CopyDraftCandidate:
        product = campaign.product_name or campaign.name
        work_order = campaign.metadata_json.get("work_order") or {}
        landing_page = campaign.metadata_json.get("landing_page") or {}
        parsed_fields = work_order.get("parsed_fields", {})
        country = work_order.get("country") or parsed_fields.get("country")
        media = work_order.get("media") or parsed_fields.get("media")
        landing_url = work_order.get("landing_url") or parsed_fields.get("landing_url")
        landing_title = landing_page.get("title")
        landing_excerpt = landing_page.get("text_excerpt")
        target_language = build_target_language_context(campaign=campaign)
        if target_language["country_code"] == "IN":
            body = (
                f"{topic.title}\n\n"
                "घर पर बड़े स्क्रीन वाला मनोरंजन आसान बनाएं। अपनी पसंद की सामग्री देखें "
                "और साफ, सरल अनुभव के साथ शुरुआत करें।\n\n"
                f"Landing page: {landing_url or 'not provided'}."
            )
            headline = f"{product} के साथ देखें"
            description = "साफ और आसान मनोरंजन अनुभव।"
        else:
            body = (
                f"{topic.title}\n\n"
                f"{topic.angle}\n\n"
                f"Market context: country={country or 'unspecified'}, "
                f"media={media or 'unspecified'}.\n\n"
                f"Landing page title: {landing_title or 'not fetched'}.\n\n"
                f"If your audience is looking for a better way to approach {product}, "
                "this campaign introduces the benefit clearly, supports it with proof, "
                "and ends with one simple call to action.\n\n"
                f"Landing page insight: {(landing_excerpt or 'not available')[:500]}\n\n"
                f"Landing page: {landing_url or 'not provided'}.\n\n"
                "Use this draft as the first human-review version before publication."
            )
            headline = f"Try {product} today"
            description = "A clear, benefit-led Facebook ad draft."
        return CopyDraftCandidate(
            body=body,
            primary_text=body[:500],
            headline=headline,
            description=description,
            cta=constraints.get("cta", "Learn More"),
        )

    async def revise_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        draft: CopyDraft,
        feedback: str,
        constraints: dict,
    ) -> CopyDraftCandidate:
        revised_body = (
            f"{draft.body}\n\nRevision note applied: {feedback}\n"
            "This version is tightened for review and publication."
        )
        return CopyDraftCandidate(
            body=revised_body,
            primary_text=revised_body[:500],
            headline=draft.headline,
            description=draft.description,
            cta=draft.cta or constraints.get("cta", "Learn More"),
        )

    async def generate_image_briefs(
        self,
        draft: CopyDraft,
        count: int,
        size: str,
        feedback: str | None = None,
        source_asset: CreativeAsset | None = None,
        storyboard_context: dict | None = None,
    ) -> list[ImageBrief]:
        snippets = [
            "Main benefit",
            "Customer pain point",
            "Proof or reason to believe",
            "Simple offer",
            "Call to action",
        ]
        target_language = build_target_language_context(draft_metadata=draft.metadata_json)
        if target_language["country_code"] == "IN":
            snippets = [
                "मुख्य फायदा",
                "आसान मनोरंजन",
                "भरोसे की वजह",
                "सरल ऑफर",
                "अभी देखें",
            ]
        briefs: list[ImageBrief] = []
        landing_page = draft.metadata_json.get("landing_page") or {}
        landing_hint = landing_page.get("title") or landing_page.get("url")
        storyboard_hint = _mock_storyboard_hint(storyboard_context)
        creative_strategy = _mock_creative_strategy(draft.metadata_json, storyboard_context)
        for index in range(count):
            snippet = snippets[index % len(snippets)]
            revision_hint = (
                f" Apply operator revision request: {feedback.strip()[:280]}."
                if feedback and feedback.strip()
                else ""
            )
            source_hint = (
                f" Revise previous version {source_asset.version}."
                if source_asset is not None
                else ""
            )
            strategy_hint = _mock_strategy_image_hint(
                creative_strategy,
                index + 1,
                storyboard_context,
            )
            visual_reference_hint = _mock_visual_reference_hint(creative_strategy)
            briefs.append(
                ImageBrief(
                    image_index=index + 1,
                    title=("Revised image" if feedback else snippet),
                    short_text=sanitize_creative_safety_text((draft.headline or snippet)[:80]),
                    visual_direction=(
                        "Clean standalone performance-ad layout with readable text, product focus, "
                        "and enough negative space for mobile feed placements. "
                        f"Use landing page context: {landing_hint or 'not available'}."
                        f"{strategy_hint}"
                        f"{visual_reference_hint}"
                        f"{storyboard_hint}"
                        f"{source_hint}{revision_hint}"
                    ),
                    size=size,
                )
            )
        return briefs

    async def generate_video_storyboard(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list[CreativeAsset],
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        instructions: str | None = None,
    ) -> VideoStoryboardCandidate:
        product = campaign.product_name or campaign.name
        safe_product = "the app lobby" if contains_creative_safety_risk(product) else product
        target_language = build_target_language_context(campaign=campaign, context=context)
        creative_strategy = _mock_creative_strategy(
            campaign.metadata_json,
            draft.metadata_json if draft else None,
            context,
            *[asset.metadata_json for asset in assets],
        )
        if (
            isinstance(creative_strategy, dict)
            and creative_strategy.get("schema_version") == "creative_strategy.v2"
        ):
            scene_count = 2 if duration_seconds <= 6 else 3 if duration_seconds <= 12 else 4
            step = max(1, duration_seconds // scene_count)
            scenes = []
            for index in range(scene_count):
                start = index * step
                end = (
                    duration_seconds
                    if index == scene_count - 1
                    else min(duration_seconds, (index + 1) * step)
                )
                scenes.append(
                    VideoStoryboardScene(
                        scene_index=index + 1,
                        start_second=start,
                        end_second=end,
                        visual=_mock_v2_strategy_scene_visual(
                            creative_strategy, index, scene_count, safe_product
                        ),
                        subtitle=(
                            "Shop Now"
                            if creative_strategy.get("vertical") == "ecommerce"
                            and index == scene_count - 1
                            else "Try Now"
                        ),
                        motion="Fast readable motion.",
                        voiceover=None,
                        source_asset_ids=[assets[index % len(assets)].id] if assets else [],
                        notes="Duration-adaptive v2 strategy scene.",
                    )
                )
            return VideoStoryboardCandidate(
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
                scenes=scenes,
                rationale=(
                    "Mock storyboard follows creative_strategy.v2 and requested duration_seconds."
                ),
            )
        asset_count = max(1, len(assets))
        scene_count = min(max(asset_count, 3), 5)
        segment = max(1, duration_seconds // scene_count)
        landing_page = context.get("landing_page") or {}
        landing_title = landing_page.get("title") or "landing page"
        scenes: list[VideoStoryboardScene] = []

        for index in range(scene_count):
            asset = assets[index % asset_count] if assets else None
            start_second = index * segment
            end_second = duration_seconds if index == scene_count - 1 else (index + 1) * segment
            if index == 0:
                visual = _mock_strategy_scene_visual(
                    creative_strategy,
                    role="first_frame",
                    fallback=f"Open with the strongest product benefit for {safe_product}.",
                )
                subtitle = (
                    f"{safe_product}: तुरंत देखें"
                    if target_language["country_code"] == "IN"
                    else f"{safe_product}: watch instantly"
                )
            elif index == scene_count - 1:
                visual = _mock_strategy_scene_visual(
                    creative_strategy,
                    role="last_frame",
                    fallback="End on a clear call to action and keep the final frame readable.",
                )
                subtitle = (
                    "Start"
                    if creative_strategy
                    else (
                        "अभी डाउनलोड करें"
                        if target_language["country_code"] == "IN"
                        else "Download Now"
                    )
                )
            else:
                visual = f"Show proof and variety using context from {landing_title[:80]}."
                subtitle = (
                    "HD में मनोरंजन देखें"
                    if target_language["country_code"] == "IN"
                    else "Free live channels in HD"
                )

            scenes.append(
                VideoStoryboardScene(
                    scene_index=index + 1,
                    start_second=start_second,
                    end_second=end_second,
                    visual=sanitize_creative_safety_text(
                        f"{visual} Use image asset {asset.id}."
                        if asset
                        else f"{visual} This scene will guide a future generated image."
                    ),
                    subtitle=sanitize_creative_safety_text(subtitle),
                    motion="Slow zoom, quick text reveal, and clean vertical-safe framing.",
                    voiceover=(
                        sanitize_creative_safety_text(draft.primary_text[:120])
                        if draft and draft.primary_text
                        else f"Discover {safe_product} in a simple, fast experience."
                    ),
                    source_asset_ids=[asset.id] if asset else [],
                    notes=instructions or "Mock storyboard for reserved video generation.",
                )
            )

        return VideoStoryboardCandidate(
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            scenes=scenes,
            rationale=(
                "Mock storyboard uses selected creative assets, copy context, and landing page "
                "signals to produce a reviewable video plan."
            ),
        )

    async def stream_video_storyboard_text(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list[CreativeAsset],
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        instructions: str | None = None,
    ) -> AsyncIterator[str]:
        storyboard = await self.generate_video_storyboard(
            campaign=campaign,
            draft=draft,
            assets=assets,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            context=context,
            instructions=instructions,
        )
        for chunk in _chunk_text(_storyboard_script_text(storyboard)):
            yield chunk

    async def revise_video_storyboard(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list[CreativeAsset],
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        current_storyboard: list[dict],
        current_storyboard_text: str | None,
        feedback: str,
    ) -> VideoStoryboardCandidate:
        allowed_asset_ids = {asset.id for asset in assets}
        scenes: list[VideoStoryboardScene] = []
        for index, item in enumerate(current_storyboard, start=1):
            if not isinstance(item, dict):
                continue
            source_asset_ids = [
                asset_id
                for asset_id in _text_list(item.get("source_asset_ids"))
                if asset_id in allowed_asset_ids
            ]
            if not source_asset_ids and assets:
                source_asset_ids = [assets[(index - 1) % len(assets)].id]
            scenes.append(
                VideoStoryboardScene(
                    scene_index=_int_or(item.get("scene_index"), index),
                    start_second=_optional_int(item.get("start_second")),
                    end_second=_optional_int(item.get("end_second")),
                    visual=f"{item.get('visual') or current_storyboard_text or 'Storyboard scene'}",
                    subtitle=item.get("subtitle"),
                    motion=item.get("motion") or "Keep motion simple and readable.",
                    voiceover=item.get("voiceover"),
                    source_asset_ids=source_asset_ids,
                    notes=f"Revision applied: {feedback}",
                )
            )

        if not scenes:
            generated = await self.generate_video_storyboard(
                campaign=campaign,
                draft=draft,
                assets=assets,
                duration_seconds=duration_seconds,
                aspect_ratio=aspect_ratio,
                context=context,
                instructions=feedback,
            )
            scenes = generated.scenes

        return VideoStoryboardCandidate(
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            scenes=scenes,
            rationale=f"Mock storyboard revision applied operator feedback: {feedback}",
        )

    async def stream_video_storyboard_revision_text(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list[CreativeAsset],
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        current_storyboard: list[dict],
        current_storyboard_text: str | None,
        feedback: str,
    ) -> AsyncIterator[str]:
        storyboard = await self.revise_video_storyboard(
            campaign=campaign,
            draft=draft,
            assets=assets,
            duration_seconds=duration_seconds,
            aspect_ratio=aspect_ratio,
            context=context,
            current_storyboard=current_storyboard,
            current_storyboard_text=current_storyboard_text,
            feedback=feedback,
        )
        for chunk in _chunk_text(_storyboard_script_text(storyboard)):
            yield chunk


def _uses_facebook_operator_result(context: dict[str, Any]) -> bool:
    contract = context.get("result_contract")
    return isinstance(contract, dict) and (
        contract.get("schema_version") == "facebook_ad_analysis_v1"
    )


def _mock_facebook_operator_result(context: dict[str, Any]) -> dict[str, Any]:
    """Return the external compact contract without claiming unobserved facts."""

    creative = context.get("creative") if isinstance(context.get("creative"), dict) else {}
    creative_type = str(creative.get("creative_type") or "image").strip().lower()
    if "video" in creative_type:
        media_type = "video"
    elif creative_type == "carousel":
        media_type = "carousel"
    else:
        media_type = "image"
    rule_analysis = (
        context.get("rule_analysis") if isinstance(context.get("rule_analysis"), dict) else {}
    )
    executive = (
        rule_analysis.get("executive_summary")
        if isinstance(rule_analysis.get("executive_summary"), dict)
        else {}
    )
    action = str(executive.get("verdict") or "monitor").strip().lower()
    if action not in {"scale", "optimize", "monitor", "pause"}:
        action = "monitor"
    priority = str(executive.get("priority") or "medium").strip().lower()
    if priority == "critical":
        priority = "high"
    if priority not in {"high", "medium", "low"}:
        priority = "medium"

    return {
        "summary": "Mock 环境已生成基础排查建议；请在真实模型环境结合完整投放数据复核。",
        "overall_decision": {
            "action": action,
            "priority": priority,
            "main_problem": "Mock 环境仅验证分析链路，需结合真实投放数据确认主要问题。",
        },
        "targeting_analysis": [],
        "adjustment_plans": [
            {
                "priority": "medium",
                "category": "tracking",
                "title": "核对关键转化事件",
                "action": "确认 Facebook Pixel、CAPI 与业务转化事件持续回传，并按广告层级核对。",
                "reason": "Mock 环境不具备真实投放诊断和受众拆分成效。",
                "expected_effect": "让后续优化可以基于真实业务结果判断。",
                "what_to_watch": "purchase、lead 等目标事件数量及单次结果成本。",
            }
        ],
        "copywriting_analysis": {
            "summary": "Mock 环境不对广告文案作真实质量判断。",
            "problems": [],
            "suggestions": ["在真实模型环境中结合原始文案和投放结果生成对照版本。"],
            "recommended_primary_text": None,
            "recommended_headline": None,
            "recommended_description": None,
        },
        "media_analysis": {
            "media_type": media_type,
            "summary": "Mock 环境不进行真实画面识别。",
            "improvements": [],
        },
        "market_intelligence": {
            "status": "unavailable",
            "summary": "Mock 环境不执行公开相似广告研究。",
            "references": [],
            "limitation": "公开来源只能用于参考创意模式，不能验证真实投放成效。",
        },
        "data_gaps": ["Mock 环境不提供真实素材识别、受众拆分或公开相似广告研究结论。"],
    }


def _mock_ad_performance_optimization_work_order(
    creative_name: str,
    creative: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    headline = creative.get("headline") or creative.get("title")
    primary_text = (
        creative.get("message")
        or creative.get("primary_text")
        or creative.get("body")
        or creative.get("text")
    )
    landing_url = creative.get("link") or creative.get("landing_url")
    image_url = (
        creative.get("image_url")
        or creative.get("imageUrl")
        or creative.get("asset_image_url")
        or creative.get("thumbnail_url")
    )
    return {
        "schema_version": "ad_performance_optimization_work_order_v1",
        "operator_summary": f"Mock AI work order for {creative_name}",
        "priority": "high",
        "overall_action": "check_landing_page_first",
        "next_step": "Check the landing page path first, then generate a clearer creative draft.",
        "modules_to_change": [
            "campaign objective",
            "optimization event",
            "headline",
            "landing page",
        ],
        "modules_to_keep": ["adset name"],
        "modules_to_watch": ["daily budget"],
        "campaign": [
            _mock_optimization_field(
                field="objective",
                label="campaign objective",
                current_value=metrics.get("campaign_objective"),
                action="rewrite",
                priority="high",
                reason=(
                    "Mock AI suggests aligning the campaign objective with the "
                    "business conversion goal."
                ),
                suggested_value="PURCHASE / LEAD / OFFSITE_CONVERSIONS",
            )
        ],
        "adset": [
            _mock_optimization_field(
                field="customEventType",
                label="optimization event",
                current_value=None,
                action="missing",
                priority="high",
                reason="Mock AI did not receive a concrete purchase, add_to_cart, or lead event.",
                suggested_value="PURCHASE",
                missing=True,
            ),
            _mock_optimization_field(
                field="daily_budget",
                label="daily budget",
                current_value=creative.get("daily_budget"),
                action="watch",
                priority="medium",
                reason=(
                    "Keep budget stable until the landing page check and creative "
                    "draft are reviewed."
                ),
            ),
        ],
        "creative": [
            _mock_optimization_field(
                field="landing_page_url",
                label="landing page URL",
                current_value=landing_url,
                action="check",
                priority="high",
                reason=(
                    "Clicks are useful only if the landing page loads and events fire correctly."
                ),
                suggested_value=landing_url,
            ),
            _mock_optimization_field(
                field="headline",
                label="headline",
                current_value=headline,
                action="regenerate",
                priority="high",
                reason="Mock AI recommends testing a stronger benefit or challenge-led headline.",
                suggested_direction="Lead with the result, challenge, or clear benefit.",
                generation_prompt=(
                    "Generate 5 Meta ad headlines with a clear result, challenge, or benefit."
                ),
                can_apply_to_generation=True,
                missing=headline is None,
            ),
            _mock_optimization_field(
                field="primary_text",
                label="primary text",
                current_value=primary_text,
                action="rewrite",
                priority="medium",
                reason="Mock AI recommends making the first sentence more direct and testable.",
                suggested_direction="State the user outcome first, then add one reason to click.",
                generation_prompt=(
                    "Rewrite the Meta ad primary text with a stronger first sentence "
                    "and action reason."
                ),
                can_apply_to_generation=True,
                missing=primary_text is None,
            ),
            _mock_optimization_field(
                field="image_material",
                label="image material",
                current_value=image_url,
                action="keep" if image_url else "missing",
                priority="medium",
                reason=(
                    "Mock AI keeps the image if the URL is present; real providers "
                    "inspect the image directly."
                ),
                missing=image_url is None,
            ),
        ],
        "warnings": [
            "Mock work orders are deterministic and only validate the data path.",
            "Use a real LLM provider for production-grade creative judgment.",
        ],
    }


def _mock_optimization_field(
    field: str,
    label: str,
    current_value: Any,
    action: str,
    priority: str,
    reason: str,
    suggested_value: Any | None = None,
    suggested_direction: str | None = None,
    generation_prompt: str | None = None,
    can_apply_to_generation: bool = False,
    missing: bool = False,
) -> dict[str, Any]:
    return {
        "field": field,
        "label": label,
        "current_value": current_value,
        "action": action,
        "priority": priority,
        "suggested_value": suggested_value,
        "suggested_direction": suggested_direction,
        "generation_prompt": generation_prompt,
        "reason": reason,
        "source": "ai",
        "can_apply_to_generation": can_apply_to_generation,
        "missing": missing,
    }


def _append_context(
    angle: str,
    country: str | None,
    event_name: str | None,
    landing_title: str | None,
) -> str:
    context_parts = []
    if country:
        context_parts.append(f"targeting {country}")
    if event_name:
        context_parts.append(f"optimized for {event_name}")
    if landing_title:
        context_parts.append(f"based on landing page '{landing_title[:80]}'")
    if not context_parts:
        return angle
    return f"{angle} Context: {', '.join(context_parts)}."


def _topic_templates(
    revision_feedback: str,
    target_language: dict[str, str],
) -> list[tuple[str, str]]:
    if target_language["country_code"] == "IN":
        if revision_feedback:
            return [
                (
                    "ऑपरेटर की राय के अनुसार नया टीवी एंगल",
                    f"Apply operator feedback: {revision_feedback}",
                ),
                (
                    "घर के मनोरंजन पर केंद्रित नया संदेश",
                    f"Reframe the strongest benefit around: {revision_feedback}",
                ),
                (
                    "साफ और सुरक्षित वैल्यू एंगल",
                    f"Use a more compliant angle while following: {revision_feedback}",
                ),
            ]
        return [
            (
                "घर पर मैच और फिल्में देखने का आसान तरीका",
                "Lead with a broad home entertainment scenario.",
            ),
            (
                "परिवार के लिए साफ और आसान टीवी अनुभव",
                "Show a family-friendly entertainment use case.",
            ),
            ("मनोरंजन के लिए स्मार्ट वैल्यू वाला टीवी", "Use credibility and practical value."),
            ("आज ही नया देखने का अनुभव शुरू करें", "Frame the message around a clear next step."),
            ("टीवी चुनते समय ध्यान देने वाली बात", "Teach one useful idea before presenting the offer."),
        ]

    if revision_feedback:
        return [
            ("Revised operator direction", f"Apply operator feedback: {revision_feedback}"),
            ("Adjusted benefit hook", f"Reframe the strongest benefit around: {revision_feedback}"),
            (
                "Cleaner review-safe angle",
                f"Use a more compliant angle while following: {revision_feedback}",
            ),
        ]

    return [
        ("Pain point hook", "Lead with the daily pain the audience already understands."),
        ("Before and after", "Show the transformation customers can expect."),
        ("Proof and trust", "Use credibility, reviews, or measurable proof."),
        ("Clear next step", "Frame the message around one practical action."),
        ("Educational angle", "Teach one useful idea before presenting the offer."),
    ]


def _text_signal(signals: dict, key: str) -> str:
    value = signals.get(key)
    return value.strip() if isinstance(value, str) else ""


def _storyboard_script_text(storyboard: VideoStoryboardCandidate) -> str:
    lines = [
        f"视频分镜脚本｜{storyboard.aspect_ratio}｜{storyboard.duration_seconds} 秒",
        "",
    ]
    for scene in storyboard.scenes:
        start_second = scene.start_second if scene.start_second is not None else "-"
        end_second = scene.end_second if scene.end_second is not None else "-"
        time_range = f"{start_second}-{end_second}s"
        source_ids = ", ".join(scene.source_asset_ids) if scene.source_asset_ids else "无"
        lines.extend(
            [
                f"第 {scene.scene_index} 幕（{time_range}）",
                f"画面：{scene.visual}",
                f"字幕：{scene.subtitle or '无'}",
                f"镜头：{scene.motion or '无'}",
                f"旁白：{scene.voiceover or '无'}",
                f"参考图：{source_ids}",
            ]
        )
        if scene.notes:
            lines.append(f"备注：{scene.notes}")
        lines.append("")
    if storyboard.rationale:
        lines.append(f"生成思路：{storyboard.rationale}")
    return "\n".join(lines).strip() + "\n"


def _mock_creative_strategy(*sources: Any) -> dict[str, Any] | None:
    for source in sources:
        if not isinstance(source, dict):
            continue
        strategy = source.get("creative_strategy")
        if isinstance(strategy, dict):
            return strategy
    return None


def _mock_strategy_image_hint(
    creative_strategy: dict[str, Any] | None,
    image_index: int,
    storyboard_context: dict | None,
) -> str:
    if not isinstance(creative_strategy, dict):
        return ""
    schema_version = creative_strategy.get("schema_version")
    if schema_version == "creative_strategy.v2":
        vertical = _mock_strategy_vertical(creative_strategy)
        image_guidance = creative_strategy.get("image_guidance")
        hooks = image_guidance.get("visual_hooks") if isinstance(image_guidance, dict) else []
        hook_text = (
            ", ".join(str(item) for item in hooks[:3] if str(item).strip())
            if isinstance(hooks, list)
            else ""
        )
        direction = (
            f" Follow creative_strategy.v2 {vertical} visual direction"
            if vertical in {"game", "ecommerce"}
            else " Follow creative_strategy.v2 general visual direction"
        )
        if hook_text:
            direction += f": {hook_text}."
        else:
            direction += "."
        market_game_hint = _mock_market_game_image_hint(creative_strategy)
        if market_game_hint:
            direction += market_game_hint
        return direction
    template_id = creative_strategy.get("template_id")
    role = _mock_keyframe_role(image_index, storyboard_context)
    concept_hint = _mock_strategy_concept_hint(
        creative_strategy,
        image_index,
        storyboard_context,
        role,
    )
    text_layout_hint = _mock_text_layout_hint(creative_strategy)
    if template_id == "mini_game_pool":
        if role == "last_frame":
            return (
                " Follow creative_strategy mini_game_pool: last-frame metallic GAJA game "
                "hub CTA beat with Start / Play Now, no visible numeric suffix, and "
                f"no visible brand-number text.{concept_hint}{text_layout_hint}"
            )
        return (
            " Follow creative_strategy mini_game_pool: first-frame mini-game challenge "
            "with small metallic GAJA corner logo, no visible numeric suffix, and no "
            f"visible brand-number text.{concept_hint}{text_layout_hint}"
        )
    if template_id == "gaja_brand":
        return ""
    return f" Follow creative_strategy {template_id}."


def _mock_visual_reference_hint(creative_strategy: dict[str, Any] | None) -> str:
    if not isinstance(creative_strategy, dict):
        return ""
    if creative_strategy.get("template_id") == "gaja_brand":
        return ""
    reference = creative_strategy.get("landing_visual_reference")
    if not isinstance(reference, dict):
        return ""
    surface_style = _mock_safe_visual_reference_values(reference.get("surface_style"))
    palette = _mock_safe_visual_reference_values(reference.get("palette"))
    parts: list[str] = []
    if surface_style:
        parts.append(f" Match landing visual style: {', '.join(surface_style)}.")
    if palette:
        parts.append(f" Use palette: {', '.join(palette)}.")
    return "".join(parts)


def _mock_safe_visual_reference_values(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    safe_values: list[str] = []
    for item in value:
        text = str(item).strip()
        if not text:
            continue
        safe_values.append(text)
        if len(safe_values) == 3:
            break
    return safe_values


def _mock_keyframe_role(image_index: int, storyboard_context: dict | None) -> str:
    keyframe_plan = (
        storyboard_context.get("keyframe_plan") if isinstance(storyboard_context, dict) else None
    )
    if not isinstance(keyframe_plan, dict):
        return "first_frame"
    frames_per_variant = _int_or(keyframe_plan.get("frames_per_variant"), 2)
    position = ((image_index - 1) % max(1, frames_per_variant)) + 1
    return "first_frame" if position == 1 else "last_frame"


def _mock_keyframe_group(image_index: int, storyboard_context: dict | None) -> int:
    keyframe_plan = (
        storyboard_context.get("keyframe_plan") if isinstance(storyboard_context, dict) else None
    )
    if not isinstance(keyframe_plan, dict):
        return 1
    frames_per_variant = max(1, _int_or(keyframe_plan.get("frames_per_variant"), 2))
    return ((image_index - 1) // frames_per_variant) + 1


def _mock_strategy_concept(
    creative_strategy: dict[str, Any] | None,
    image_index: int,
    storyboard_context: dict | None,
) -> dict[str, Any] | None:
    if not isinstance(creative_strategy, dict):
        return None
    concepts = creative_strategy.get("visual_concepts")
    if not isinstance(concepts, list) or not concepts:
        return None
    group = _mock_keyframe_group(image_index, storyboard_context)
    concept = concepts[min(max(group, 1), len(concepts)) - 1]
    return concept if isinstance(concept, dict) else None


def _mock_first_strategy_concept(
    creative_strategy: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(creative_strategy, dict):
        return None
    concepts = creative_strategy.get("visual_concepts")
    if not isinstance(concepts, list) or not concepts:
        return None
    concept = concepts[0]
    return concept if isinstance(concept, dict) else None


def _mock_strategy_concept_hint(
    creative_strategy: dict[str, Any] | None,
    image_index: int,
    storyboard_context: dict | None,
    role: str,
) -> str:
    concept = _mock_strategy_concept(creative_strategy, image_index, storyboard_context)
    if not concept:
        return ""
    concept_id = _mock_safe_strategy_text(concept.get("concept_id"))
    name = _mock_safe_strategy_text(concept.get("name"))
    theme = _mock_safe_strategy_text(concept.get("visual_theme"))
    role_visual_key = "last_frame_visual" if role == "last_frame" else "first_frame_visual"
    role_visual = _mock_safe_strategy_text(concept.get(role_visual_key))
    pieces = [
        f"variant concept {concept_id}" if concept_id else "",
        f"({name})" if name else "",
        f"theme: {theme}" if theme else "",
        f"{role.replace('_', '-')}: {role_visual}" if role_visual else "",
    ]
    concept_text = "; ".join(piece for piece in pieces if piece)
    return f" Use {concept_text}." if concept_text else ""


def _mock_text_layout_hint(creative_strategy: dict[str, Any] | None) -> str:
    if not isinstance(creative_strategy, dict):
        return ""
    layout = creative_strategy.get("text_layout_rules")
    if not isinstance(layout, dict):
        return ""
    instruction = _mock_safe_strategy_text(layout.get("layout_instruction"))
    if instruction:
        return f" Text layout: {instruction}"
    return " Text layout: keep all visible text inside the safe area with no overflow."


def _mock_safe_strategy_text(value: Any) -> str:
    text = sanitize_creative_safety_text(str(value or "").strip())
    if not text:
        return ""
    return text


def _mock_strategy_scene_visual(
    creative_strategy: dict[str, Any] | None,
    role: str,
    fallback: str,
) -> str:
    if not isinstance(creative_strategy, dict):
        return fallback
    template_id = creative_strategy.get("template_id")
    concept = _mock_first_strategy_concept(creative_strategy)
    concept_visual = _mock_safe_strategy_text(
        concept.get("last_frame_visual" if role == "last_frame" else "first_frame_visual")
        if concept
        else ""
    )
    layout_hint = _mock_text_layout_hint(creative_strategy).strip()
    if template_id == "mini_game_pool":
        if role == "last_frame":
            return (
                "End on a metallic GAJA game hub with multiple mini-game challenge tiles, "
                "Start CTA, "
                "no visible numeric suffix, and no visible brand-number text. "
                f"{concept_visual} {layout_hint}".strip()
            )
        return (
            "Open with a playable mini-game challenge, small metallic GAJA corner logo, "
            "fast curiosity hook, no visible numeric suffix, and no visible brand-number text. "
            f"{concept_visual} {layout_hint}".strip()
        )
    if template_id == "gaja_brand":
        return fallback
    return fallback


def _mock_v2_strategy_scene_visual(
    creative_strategy: dict[str, Any],
    index: int,
    scene_count: int,
    product: str,
) -> str:
    vertical = _mock_strategy_vertical(creative_strategy)
    market_game_visual = _mock_market_game_scene_visual(
        creative_strategy,
        index=index,
        scene_count=scene_count,
        product=product,
    )
    if market_game_visual:
        return market_game_visual
    if vertical == "game":
        beats = ["challenge hook", "failure moment", "correct move", "reward payoff"]
    else:
        beats = ["pain point scene", "product appears", "benefit demonstration", "clear CTA"]
    beat = beats[min(index, len(beats) - 1)]
    if index == scene_count - 1:
        beat = "clear CTA"
    return f"{product}: {beat} following creative_strategy.v2."


def _mock_market_game_image_hint(creative_strategy: dict[str, Any]) -> str:
    pack = creative_strategy.get("market_game_style_pack")
    if not isinstance(pack, dict):
        return ""
    visual_world = _mock_safe_strategy_values(pack.get("visual_world"), limit=3)
    aaa = pack.get("aaa_game_inspiration")
    archetypes = (
        _mock_safe_strategy_values(aaa.get("genre_archetypes"), limit=3)
        if isinstance(aaa, dict)
        else []
    )
    gameplay = pack.get("gameplay_process")
    actions = (
        _mock_safe_strategy_values(gameplay.get("player_actions"), limit=2)
        if isinstance(gameplay, dict)
        else []
    )
    cultural = pack.get("cultural_safety")
    avoid = (
        _mock_safe_strategy_values(cultural.get("avoid"), limit=2)
        if isinstance(cultural, dict)
        else []
    )
    pieces: list[str] = []
    if visual_world:
        pieces.append(f" visual world: {', '.join(visual_world)}")
    if archetypes:
        pieces.append(f" AAA-style archetypes: {', '.join(archetypes)}")
    if actions:
        pieces.append(f" player action: {', '.join(actions)}")
    if avoid:
        pieces.append(f" cultural safety avoid: {', '.join(avoid)}")
    return "." + ";".join(pieces) + "." if pieces else ""


def _mock_market_game_scene_visual(
    creative_strategy: dict[str, Any],
    *,
    index: int,
    scene_count: int,
    product: str,
) -> str:
    if _mock_strategy_vertical(creative_strategy) != "game":
        return ""
    pack = creative_strategy.get("market_game_style_pack")
    if not isinstance(pack, dict):
        return ""
    interests = _mock_safe_strategy_values(pack.get("game_interest_hypothesis"), limit=2)
    visual_world = _mock_safe_strategy_values(pack.get("visual_world"), limit=2)
    aaa = pack.get("aaa_game_inspiration")
    archetypes = (
        _mock_safe_strategy_values(aaa.get("genre_archetypes"), limit=2)
        if isinstance(aaa, dict)
        else []
    )
    gameplay = pack.get("gameplay_process")
    if not isinstance(gameplay, dict):
        gameplay = {}
    goal = _mock_safe_strategy_text(gameplay.get("player_goal"))
    conflict = _mock_safe_strategy_text(gameplay.get("opening_conflict"))
    actions = _mock_safe_strategy_values(gameplay.get("player_actions"), limit=4)
    feedback = _mock_safe_strategy_values(gameplay.get("progression_feedback"), limit=4)
    ending = _mock_safe_strategy_text(gameplay.get("ending_transition"))

    base = [
        product,
        ", ".join(archetypes) if archetypes else "cinematic gameplay challenge",
        ", ".join(visual_world) if visual_world else "",
        ", ".join(interests) if interests else "",
    ]
    if index == 0:
        beat = f"player goal: {goal}; opening conflict: {conflict}"
    elif index == scene_count - 1:
        action = actions[min(index, len(actions) - 1)] if actions else "complete the challenge"
        progress = feedback[min(index, len(feedback) - 1)] if feedback else "unlock glow appears"
        beat = (
            f"player action: {action}; progression feedback: {progress}; "
            f"ending transition: {ending}"
        )
    else:
        action = actions[min(index, len(actions) - 1)] if actions else "choose the right move"
        progress = feedback[min(index, len(feedback) - 1)] if feedback else "progress bar fills"
        beat = f"player action: {action}; progression feedback: {progress}"
    return ". ".join(part for part in [": ".join(item for item in base if item), beat] if part)


def _mock_safe_strategy_values(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    safe_values = [
        safe_item
        for item in value
        if str(item).strip()
        for safe_item in [_mock_safe_strategy_text(item)]
        if safe_item
    ]
    return safe_values[:limit]


def _mock_strategy_vertical(creative_strategy: dict[str, Any]) -> str:
    vertical = str(creative_strategy.get("vertical") or "").strip().casefold()
    if vertical == "gambling":
        return "gambling"
    if vertical == "game":
        return "game"
    return "ecommerce"


def _mock_storyboard_hint(storyboard_context: dict | None) -> str:
    if not isinstance(storyboard_context, dict):
        return ""
    storyboard_text = str(storyboard_context.get("storyboard_text") or "").strip()
    if storyboard_text:
        return f" Align with storyboard: {storyboard_text[:320]}."

    scenes = storyboard_context.get("storyboard")
    if isinstance(scenes, list) and scenes:
        first_scene = scenes[0] if isinstance(scenes[0], dict) else {}
        visual = str(first_scene.get("visual") or "").strip()
        if visual:
            return f" Align with storyboard scene: {visual[:240]}."
    return ""


def _chunk_text(value: str, size: int = 24) -> list[str]:
    return [value[index : index + size] for index in range(0, len(value), size)]


def _text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if item]
    return [str(value)]


def _int_or(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _field(
    value: Any | None,
    status: str,
    confidence: float,
    evidence: list[Any],
    candidates: list[Any],
    reason: str,
    normalized_value: Any | None = None,
) -> dict[str, Any]:
    return {
        "value": value,
        "normalized_value": normalized_value if normalized_value is not None else value,
        "status": status,
        "confidence": max(0, min(1, confidence)),
        "evidence": [str(item) for item in evidence if item],
        "candidates": [item for item in candidates if item],
        "reason": reason,
    }


def _find_urls(raw_content: str) -> list[str]:
    urls = re.findall(r"https?://[^\s，,。；;）)】\]]+", raw_content, flags=re.I)
    return list(dict.fromkeys(url.strip() for url in urls))


COUNTRY_ALIASES = {
    "印度": "印度",
    "india": "印度",
    "in": "印度",
    "美国": "美国",
    "usa": "美国",
    "us": "美国",
    "united states": "美国",
    "菲律宾": "菲律宾",
    "philippines": "菲律宾",
    "印尼": "印度尼西亚",
    "印度尼西亚": "印度尼西亚",
    "indonesia": "印度尼西亚",
    "泰国": "泰国",
    "thailand": "泰国",
    "越南": "越南",
    "vietnam": "越南",
    "马来西亚": "马来西亚",
    "malaysia": "马来西亚",
    "新加坡": "新加坡",
    "singapore": "新加坡",
    "巴西": "巴西",
    "brazil": "巴西",
    "墨西哥": "墨西哥",
    "mexico": "墨西哥",
}


COUNTRY_CODES = {
    "印度": "IN",
    "美国": "US",
    "菲律宾": "PH",
    "印度尼西亚": "ID",
    "泰国": "TH",
    "越南": "VN",
    "马来西亚": "MY",
    "新加坡": "SG",
    "巴西": "BR",
    "墨西哥": "MX",
}


def _find_country_candidates(raw_content: str) -> list[str]:
    lowered = raw_content.lower()
    found: list[str] = []
    for alias, country in COUNTRY_ALIASES.items():
        pattern = rf"(?<![a-z]){re.escape(alias.lower())}(?![a-z])"
        if re.search(pattern, lowered):
            found.append(country)
    return list(dict.fromkeys(found))


def _normalize_country(country: str | None) -> str | None:
    if not country:
        return None
    return COUNTRY_CODES.get(country, country)


def _find_event(raw_content: str) -> tuple[str, str, str]:
    normalized = re.sub(r"\s+", "", raw_content.lower())
    if any(keyword in normalized for keyword in ["购物", "购买", "下单", "purchase", "shop"]):
        return "购物", "extracted", "识别到购买/购物意图。"
    if any(keyword in normalized for keyword in ["加购", "addtocart", "cart"]):
        return "加购", "extracted", "识别到加购意图。"
    if any(keyword in normalized for keyword in ["注册", "线索", "lead", "signup"]):
        return "线索", "extracted", "识别到注册/线索意图。"
    if any(keyword in normalized for keyword in ["流量", "点击", "traffic", "click"]):
        return "流量", "extracted", "识别到流量/点击目标。"
    return "流量", "suggested", "未明确写投放事件，默认建议流量。"


def _normalize_event(event_name: str | None) -> str | None:
    if event_name == "购物":
        return "purchase"
    if event_name == "加购":
        return "add_to_cart"
    if event_name == "线索":
        return "lead"
    if event_name == "流量":
        return "traffic"
    return event_name


def _find_age_range(raw_content: str) -> tuple[int | None, int | None]:
    pattern = re.compile(r"(?:年龄|age)?[^\d]{0,8}(\d{2})\s*(?:-|~|至|到|—|–)\s*(\d{2})", re.I)
    match = pattern.search(raw_content)
    if not match:
        return None, None
    age_min = int(match.group(1))
    age_max = int(match.group(2))
    if 13 <= age_min <= age_max <= 65:
        return age_min, age_max
    return None, None


def _find_gender(raw_content: str) -> str | None:
    lowered = raw_content.lower()
    has_female = "女" in lowered or re.search(r"\b(female|women|woman)\b", lowered) is not None
    has_male = "男" in lowered or re.search(r"\b(male|men|man)\b", lowered) is not None
    if has_male and not has_female:
        return "男"
    if has_female and not has_male:
        return "女"
    if any(keyword in lowered for keyword in ["不限", "all gender", "all genders"]):
        return "不限"
    return None


def _normalize_gender(gender: str | None) -> str:
    if gender == "男":
        return "male"
    if gender == "女":
        return "female"
    return "all"


def _find_audience_raw(
    raw_content: str,
    age_min: int | None,
    age_max: int | None,
    gender: str | None,
) -> str | None:
    for line in raw_content.replace("\r\n", "\n").split("\n"):
        if any(keyword in line for keyword in ["投放人群", "目标人群", "受众", "人群"]):
            return line.strip()
    parts = []
    if gender:
        parts.append(gender)
    if age_min and age_max:
        parts.append(f"年龄{age_min}-{age_max}")
    return "，".join(parts) if parts else None
