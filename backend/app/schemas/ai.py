from typing import Literal

from pydantic import BaseModel, Field, model_validator


class TopicCandidate(BaseModel):
    title: str
    angle: str
    angle_type: str | None = None
    audience: str | None = None
    selling_points: list[str] = Field(default_factory=list)
    risk_notes: str | None = None
    rationale: str | None = None
    score: float | None = Field(default=None, ge=0, le=1)


class CopyDraftCandidate(BaseModel):
    body: str
    primary_text: str | None = None
    headline: str | None = None
    description: str | None = None
    cta: str | None = None


class ImageBrief(BaseModel):
    image_index: int
    title: str
    short_text: str
    visual_direction: str
    size: str = "1:1"
    raw_prompt: str | None = None
    reference_image_data_url: str | None = None
    reference_image_url: str | None = None
    revision_instruction: str | None = None


class VideoStoryboardScene(BaseModel):
    scene_index: int
    start_second: int | None = None
    end_second: int | None = None
    visual: str
    subtitle: str | None = None
    motion: str | None = None
    voiceover: str | None = None
    source_asset_ids: list[str] = Field(default_factory=list)
    notes: str | None = None


class VideoStoryboardCandidate(BaseModel):
    duration_seconds: int
    aspect_ratio: str
    scenes: list[VideoStoryboardScene] = Field(default_factory=list)
    rationale: str | None = None


class FrameVisualFacts(BaseModel):
    visible_subjects: list[str] = Field(default_factory=list)
    visible_text: list[str] = Field(default_factory=list)
    environment: str = ""
    composition: str = ""
    camera_perspective: str = ""
    visual_style: str = ""
    color_and_lighting: str = ""
    opening_state: str | None = None
    ending_state: str | None = None


class FrameTransitionBrief(BaseModel):
    shared_visual_facts: list[str] = Field(default_factory=list)
    continuity_requirements: list[str] = Field(default_factory=list)
    visual_transition: str = ""
    narrative_arc: str = ""


class FrameLanguageAnalysis(BaseModel):
    first_frame_visible_languages: list[str] = Field(default_factory=list)
    last_frame_visible_languages: list[str] = Field(default_factory=list)
    recommended_output_language: str = ""
    reason: str = ""


class ReferenceVideoFrame(BaseModel):
    timestamp_seconds: float = Field(ge=0)
    image_url: str = Field(min_length=1)
    selection_reason: Literal["baseline", "opening", "ending", "high_change"] = "baseline"


class ReferenceSubjectPresence(BaseModel):
    state: str = ""
    visibility: str = ""
    screen_position: str = ""
    movement: str = ""
    appearance: str = ""
    action: str = ""
    interaction: str = ""


class ReferenceCameraPattern(BaseModel):
    movement: str = ""
    intensity: str = ""


class ReferenceTransitionPattern(BaseModel):
    type: str = ""
    description: str = ""


class ReferenceVideoSegment(BaseModel):
    start_second: float = Field(ge=0)
    end_second: float = Field(ge=0)
    subject_presence: ReferenceSubjectPresence
    camera: ReferenceCameraPattern
    transition: ReferenceTransitionPattern
    effects: list[str] = Field(default_factory=list)
    confidence: str = ""


class ReferenceBehaviorBeat(BaseModel):
    beat_id: str = Field(min_length=1)
    reference_start_second: float = Field(ge=0)
    reference_end_second: float = Field(gt=0)
    description: str = Field(min_length=1)
    visible_evidence: list[str] = Field(default_factory=list)
    behavior_type: Literal["action", "state", "overlay"] = "action"
    importance: Literal["core", "supporting", "decorative"] = "supporting"
    minimum_readable_duration_seconds: float = Field(default=0.5, gt=0)
    depends_on: list[str] = Field(default_factory=list)
    must_remain_visible_until_final: bool = False
    locked_text: str | None = None

    @model_validator(mode="after")
    def validate_reference_window(self) -> "ReferenceBehaviorBeat":
        if self.reference_end_second <= self.reference_start_second:
            raise ValueError("reference behavior beat end must be after its start")
        if self.must_remain_visible_until_final and self.behavior_type != "overlay":
            raise ValueError("only visual overlays may remain visible until the final frame")
        if self.locked_text and self.behavior_type != "overlay":
            raise ValueError("only visual overlays may define locked text")
        return self


class ReferenceBehaviorGraph(BaseModel):
    entities: list[str] = Field(default_factory=list)
    beats: list[ReferenceBehaviorBeat] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_dependencies(self) -> "ReferenceBehaviorGraph":
        beat_ids = [beat.beat_id for beat in self.beats]
        if len(beat_ids) != len(set(beat_ids)):
            raise ValueError("reference behavior beat ids must be unique")
        known_ids = set(beat_ids)
        for beat in self.beats:
            unknown_dependencies = set(beat.depends_on) - known_ids
            if unknown_dependencies:
                raise ValueError("reference behavior beat dependencies must reference known beats")
        return self


class TimelineAdaptationBeat(BaseModel):
    beat_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    target_start_second: float = Field(ge=0)
    target_end_second: float = Field(gt=0)
    importance: Literal["core", "supporting", "decorative"] = "supporting"
    depends_on: list[str] = Field(default_factory=list)
    must_remain_visible_until_final: bool = False
    locked_text: str | None = None
    adaptation_instruction: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_target_window(self) -> "TimelineAdaptationBeat":
        if self.target_end_second <= self.target_start_second:
            raise ValueError("target timeline beat end must be after its start")
        return self


class TimelineAdaptationPlan(BaseModel):
    reference_duration_seconds: float = Field(gt=0)
    target_duration_seconds: float = Field(gt=0)
    beats: list[TimelineAdaptationBeat] = Field(default_factory=list)
    adaptation_risks: list[str] = Field(default_factory=list)


class ReferenceVisualIdentityMapping(BaseModel):
    reference_element: str = Field(min_length=1)
    element_type: Literal[
        "character",
        "appearance",
        "prop",
        "product",
        "brand",
        "text",
        "reward",
        "setting",
        "other",
    ] = "other"
    strategy: Literal[
        "preserve",
        "replace_with_target",
        "morph_to_target",
        "endpoint_only",
        "preserve_through_last_anchor",
    ]
    target_first_frame_equivalent: str | None = None
    target_last_frame_equivalent: str | None = None
    instruction: str = Field(min_length=1)


class ReferenceConstraint(BaseModel):
    strength: Literal["required", "preferred"]
    instruction: str = Field(min_length=1)


class ReferenceAdaptedConstraints(BaseModel):
    subject_presence: ReferenceConstraint
    camera_pattern: ReferenceConstraint
    transition_pattern: ReferenceConstraint
    effects_pattern: ReferenceConstraint

    @model_validator(mode="after")
    def validate_strengths(self) -> "ReferenceAdaptedConstraints":
        for constraint in (
            self.subject_presence,
            self.camera_pattern,
            self.transition_pattern,
            self.effects_pattern,
        ):
            if constraint.strength != "preferred":
                raise ValueError("reference video constraints must be preferred")
        return self


class ReferenceVideoAnalysis(BaseModel):
    duration_seconds: float = Field(gt=0, le=30)
    sample_interval_seconds: float = Field(gt=0)
    segments: list[ReferenceVideoSegment] = Field(min_length=1)
    visual_identity_mappings: list[ReferenceVisualIdentityMapping] = Field(default_factory=list)
    adapted_constraints: ReferenceAdaptedConstraints
    behavior_graph: ReferenceBehaviorGraph | None = None


DirectorTensionStage = Literal["setup", "trigger", "escalation", "climax", "resolution"]


class DirectorBeat(BaseModel):
    beat_id: str = Field(min_length=1)
    stage: DirectorTensionStage
    source_evidence: list[str] = Field(default_factory=list)
    start_ratio: float = Field(default=0, ge=0, le=1)
    end_ratio: float = Field(default=1, ge=0, le=1)
    attention_objective: str = ""
    camera_instruction: str = ""
    action_requirement: str = ""
    effect_requirement: str = ""
    importance: Literal["core", "supporting", "decorative"] = "core"
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_director_beat(self) -> "DirectorBeat":
        if self.end_ratio <= self.start_ratio:
            raise ValueError("director beat end ratio must be after its start ratio")
        if self.stage == "climax" and not self.source_evidence:
            raise ValueError("climax beat requires source evidence")
        return self


class DirectorOverlayInstruction(BaseModel):
    reference_element: str = Field(min_length=1)
    strategy: Literal["inherit", "replace_with_target", "persist_to_final", "omit"]
    timing_instruction: str = Field(min_length=1)
    final_frame_requirement: str = Field(min_length=1)


DirectorActionArcPhase = Literal[
    "anchor_hold",
    "departure",
    "preparation",
    "action",
    "impact",
    "payoff",
    "return",
    "final_lock",
]

DirectorSignatureTransferRole = Literal[
    "causal_setup",
    "primary_action",
    "interaction",
    "impact",
    "visible_result",
    "camera_emphasis",
    "effect_emphasis",
    "overlay_lifecycle",
    "other",
]


DirectorOmissionInfeasibilityCategory = Literal[
    "target_capability_unavailable",
    "mechanism_unavailable",
    "identity_semantics_conflict",
    "causal_equivalent_unavailable",
    "endpoint_constraint_only",
]


DirectorOmissionInfeasibilityBasis = Literal[
    "target_capability",
    "mechanism",
    "identity_semantics",
    "causal_equivalent",
]
DirectorEvidencePolarity = Literal["affirmed", "negated"]
DirectorEvidenceScope = Literal["global", "action_interval", "endpoint_only"]

_OMISSION_CATEGORY_BASIS: dict[str, str] = {
    "target_capability_unavailable": "target_capability",
    "mechanism_unavailable": "mechanism",
    "identity_semantics_conflict": "identity_semantics",
    "causal_equivalent_unavailable": "causal_equivalent",
}


class DirectorOmissionInfeasibilityFact(BaseModel):
    category: DirectorOmissionInfeasibilityCategory
    basis: DirectorOmissionInfeasibilityBasis
    polarity: DirectorEvidencePolarity
    scope: DirectorEvidenceScope
    detail: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_fact(self) -> "DirectorOmissionInfeasibilityFact":
        expected_basis = _OMISSION_CATEGORY_BASIS.get(self.category)
        if expected_basis is None or self.basis != expected_basis:
            raise ValueError("omission infeasibility category and basis must match")
        if not self.detail.strip():
            raise ValueError("omission infeasibility detail must not be blank")
        return self


class DirectorActionArcWindow(BaseModel):
    window_id: str = Field(min_length=1)
    phase: DirectorActionArcPhase
    start_ratio: float = Field(ge=0, le=1)
    end_ratio: float = Field(gt=0, le=1)
    objective: str = Field(min_length=1)
    subject_motion_intensity: float = Field(ge=0, le=1)
    camera_intensity: float = Field(ge=0, le=1)
    effect_intensity: float = Field(ge=0, le=1)
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_window(self) -> "DirectorActionArcWindow":
        if self.end_ratio <= self.start_ratio:
            raise ValueError("action arc window end ratio must be after its start ratio")
        return self


DirectorActionCorrectionType = Literal[
    "execution",
    "payoff",
    "return",
    "support",
    "action_arc",
]


class DirectorActionCorrection(BaseModel):
    correction_type: DirectorActionCorrectionType
    signature_moment_ids: list[str] = Field(min_length=1)
    source_behavior_beat_ids: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1)


class DirectorActionCoverageReview(BaseModel):
    status: Literal["pass", "corrective", "unrecoverable"]
    required_core_behavior_beat_ids: list[str] = Field(default_factory=list)
    covered_core_behavior_beat_ids: list[str] = Field(default_factory=list)
    validly_omitted_core_behavior_beat_ids: list[str] = Field(default_factory=list)
    uncovered_core_behavior_beat_ids: list[str] = Field(default_factory=list)
    invalid_omission_moment_ids: list[str] = Field(default_factory=list)
    missing_execution_detail_moment_ids: list[str] = Field(default_factory=list)
    missing_return_moment_ids: list[str] = Field(default_factory=list)
    correction_requirements: list[str] = Field(default_factory=list)
    structured_corrections: list[DirectorActionCorrection] = Field(default_factory=list)
    unrecoverable_reasons: list[str] = Field(default_factory=list)


class DirectorSignatureMoment(BaseModel):
    moment_id: str = Field(min_length=1)
    moment_type: Literal["camera", "action", "effect", "result", "combined"]
    source_evidence: list[str] = Field(min_length=1)
    source_behavior_beat_ids: list[str] = Field(default_factory=list)
    transfer_role: DirectorSignatureTransferRole = "other"
    strategy: Literal["preserve", "adapt", "replace_with_equivalent", "omit"]
    target_adaptation: str = ""
    adapted_action: str = ""
    temporary_divergence: str = ""
    camera_support: str = ""
    effect_support: str = ""
    visible_payoff: str = ""
    return_strategy: str = ""
    assigned_beat_id: str | None = None
    omission_reason: str | None = None
    equivalent_replacement_failure: str | None = None
    literal_infeasibility_category: DirectorOmissionInfeasibilityCategory | None = None
    literal_infeasibility_evidence: str | None = None
    equivalent_infeasibility_category: DirectorOmissionInfeasibilityCategory | None = None
    equivalent_infeasibility_evidence: str | None = None
    literal_infeasibility_fact: DirectorOmissionInfeasibilityFact | None = None
    equivalent_infeasibility_fact: DirectorOmissionInfeasibilityFact | None = None

    @model_validator(mode="after")
    def validate_signature_moment(self) -> "DirectorSignatureMoment":
        if any(not evidence.strip() for evidence in self.source_evidence):
            raise ValueError("signature moment source evidence must not be blank")
        if any(not beat_id.strip() for beat_id in self.source_behavior_beat_ids):
            raise ValueError("signature moment source behavior beat ids must not be blank")
        if self.strategy != "omit":
            if not self.assigned_beat_id or not self.assigned_beat_id.strip():
                raise ValueError("non-omitted signature moment requires an assigned beat")
            if not self.adapted_action.strip():
                raise ValueError("non-omitted signature moment requires an adapted action")
            if not self.visible_payoff.strip():
                raise ValueError("non-omitted signature moment requires a visible payoff")
            if not self.return_strategy.strip():
                raise ValueError("non-omitted signature moment requires a return strategy")
            if (
                self.transfer_role in {"primary_action", "interaction", "impact"}
                and not self.temporary_divergence.strip()
            ):
                raise ValueError(
                    "action, interaction, and impact signature moments require temporary divergence"
                )
            if self.moment_type in {"camera", "combined"} and not self.camera_support.strip():
                raise ValueError("camera and combined signature moments require camera support")
            if self.moment_type in {"effect", "combined"} and not self.effect_support.strip():
                raise ValueError("effect and combined signature moments require effect support")
        else:
            if self.literal_infeasibility_fact is None:
                raise ValueError(
                    "omitted signature moment requires structured literal infeasibility fact"
                )
            if self.equivalent_infeasibility_fact is None:
                raise ValueError(
                    "omitted signature moment requires structured equivalent infeasibility fact"
                )
            if self.literal_infeasibility_fact.category not in {
                "target_capability_unavailable",
                "mechanism_unavailable",
                "identity_semantics_conflict",
            }:
                raise ValueError(
                    "literal infeasibility fact must describe a literal target limitation"
                )
            if (
                self.equivalent_infeasibility_fact.category
                != "causal_equivalent_unavailable"
            ):
                raise ValueError(
                    "equivalent infeasibility fact must describe causal equivalent unavailability"
                )
        return self


class FrameAnchoredDirectorPlan(BaseModel):
    narrative_objective: str = Field(min_length=1)
    attention_path: list[str] = Field(min_length=1)
    tension_curve: list[DirectorTensionStage] = Field(min_length=1)
    climax_beats: list[DirectorBeat] = Field(min_length=1)
    overlay_lifecycle_plan: list[DirectorOverlayInstruction] = Field(default_factory=list)
    action_arc_windows: list[DirectorActionArcWindow] = Field(default_factory=list)
    signature_moment_plan: list[DirectorSignatureMoment] = Field(default_factory=list)
    final_anchor_return: str = ""
    anchor_adaptation_plan: list[str] = Field(min_length=1)
    anti_flattening_constraints: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_director_plan(self) -> "FrameAnchoredDirectorPlan":
        stage_order = {
            "setup": 0,
            "trigger": 1,
            "escalation": 2,
            "climax": 3,
            "resolution": 4,
        }
        stage_positions = [stage_order[stage] for stage in self.tension_curve]
        if stage_positions != sorted(stage_positions):
            raise ValueError("director tension stages must be in ascending order")
        if "climax" not in self.tension_curve:
            raise ValueError("director tension curve requires a climax stage")
        beats_by_id = {beat.beat_id: beat for beat in self.climax_beats}
        for beat in self.climax_beats:
            if beat.stage != "climax":
                raise ValueError("director climax beats must use climax stage")
            if not beat.source_evidence:
                raise ValueError("climax beat requires source evidence")
        known_window_ids: set[str] = set()
        previous_window: DirectorActionArcWindow | None = None
        for window in self.action_arc_windows:
            if window.window_id in known_window_ids:
                raise ValueError("action arc windows must use unique ordered ids")
            if any(dependency not in known_window_ids for dependency in window.depends_on):
                raise ValueError("action arc dependencies must reference earlier known windows")
            if previous_window is not None and (
                window.start_ratio < previous_window.start_ratio
                or (
                    window.start_ratio == previous_window.start_ratio
                    and window.end_ratio < previous_window.end_ratio
                )
            ):
                raise ValueError("action arc windows must be declared in chronological order")
            known_window_ids.add(window.window_id)
            previous_window = window
        requires_final_anchor_return = bool(self.action_arc_windows) or any(
            moment.strategy != "omit" for moment in self.signature_moment_plan
        )
        if requires_final_anchor_return and not self.final_anchor_return.strip():
            raise ValueError(
                "director plan requires a final anchor return when action execution is declared"
            )
        signature_ids: set[str] = set()
        for moment in self.signature_moment_plan:
            if moment.moment_id in signature_ids:
                raise ValueError("director signature moment ids must be unique")
            signature_ids.add(moment.moment_id)
            if moment.strategy == "omit":
                continue
            assigned_beat = beats_by_id.get(moment.assigned_beat_id or "")
            if assigned_beat is None:
                raise ValueError(
                    "signature moment must reference an existing director beat"
                )
            if assigned_beat.importance != "core":
                raise ValueError(
                    "signature moment must be assigned to a core director beat"
                )
        if any(not constraint.strip() for constraint in self.anti_flattening_constraints):
            raise ValueError("director anti-flattening constraints must not be blank")
        return self


class FrameAnalysis(BaseModel):
    first_frame: FrameVisualFacts
    last_frame: FrameVisualFacts
    transition_brief: FrameTransitionBrief
    language_analysis: FrameLanguageAnalysis
    reference_video_analysis: ReferenceVideoAnalysis | None = None
    timeline_adaptation_plan: TimelineAdaptationPlan | None = None
    director_plan: FrameAnchoredDirectorPlan | None = None


class StoryboardSoundDesign(BaseModel):
    music: str | None = None
    ambience: str | None = None


StoryboardExecutionExecutorKind = Literal[
    "target_subject",
    "target_object",
    "target_state",
    "camera_support",
    "effect_support",
    "environment_support",
]
StoryboardExecutionAssertion = Literal["affirmed", "negated", "static"]


class StoryboardExecutionEvidence(BaseModel):
    executor_kind: StoryboardExecutionExecutorKind
    assertion: StoryboardExecutionAssertion
    action_or_state_change: str = Field(min_length=1)
    signature_moment_ids: list[str] = Field(default_factory=list)
    source_behavior_beat_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_execution_evidence(self) -> "StoryboardExecutionEvidence":
        if not self.action_or_state_change.strip():
            raise ValueError("execution evidence action or state change must not be blank")
        if any(not value.strip() for value in self.signature_moment_ids):
            raise ValueError("execution evidence signature moment ids must not be blank")
        if any(not value.strip() for value in self.source_behavior_beat_ids):
            raise ValueError("execution evidence source behavior beat ids must not be blank")
        return self


class FrameAnchoredStoryboardScene(BaseModel):
    scene_index: int
    # Target scene windows may use sub-second boundaries after adapting a reference
    # video's observed behavior to a different requested duration.
    start_second: float | None = None
    end_second: float | None = None
    frame_anchor: Literal["first_frame", "transition", "last_frame"]
    visual: str
    motion: str | None = None
    transition_goal: str | None = None
    subtitle: str | None = None
    voiceover: str | None = None
    sound_effects: list[str] = Field(default_factory=list)
    notes: str | None = None
    cinematic_beat: str | None = None
    cinematic_beats: list[str] = Field(default_factory=list)
    signature_moment_ids: list[str] = Field(default_factory=list)
    source_behavior_beat_ids: list[str] = Field(default_factory=list)
    execution_evidence: list[StoryboardExecutionEvidence] = Field(default_factory=list)
    camera_instruction: str | None = None
    tension_stage: DirectorTensionStage | None = None
    action_result_requirement: str | None = None
    effect_timing: str | None = None
    subject_motion_intensity: float | None = Field(default=None, ge=0, le=1)
    camera_intensity: float | None = Field(default=None, ge=0, le=1)
    effect_intensity: float | None = Field(default=None, ge=0, le=1)
    anchor_return_instruction: str | None = None
    # Preserve provider-flattened director prose rather than dropping its intent.
    # The external contract is the rendered storyboard_text.
    overlay_instruction: DirectorOverlayInstruction | str | None = None
    anti_flattening_requirement: str | None = None

    @model_validator(mode="after")
    def validate_execution_evidence_consistency(self) -> "FrameAnchoredStoryboardScene":
        assertions_by_claim: dict[tuple[object, ...], str] = {}
        for evidence in self.execution_evidence:
            claim_key = (
                evidence.executor_kind,
                evidence.action_or_state_change.strip().casefold(),
                tuple(sorted(set(evidence.signature_moment_ids))),
                tuple(sorted(set(evidence.source_behavior_beat_ids))),
            )
            previous = assertions_by_claim.get(claim_key)
            if previous is not None and previous != evidence.assertion:
                raise ValueError("contradictory execution evidence")
            assertions_by_claim[claim_key] = evidence.assertion
        return self


class FrameAnchoredStoryboard(BaseModel):
    duration_seconds: int
    aspect_ratio: str
    scenes: list[FrameAnchoredStoryboardScene] = Field(default_factory=list)
    sound_design: StoryboardSoundDesign = Field(default_factory=StoryboardSoundDesign)
    rationale: str | None = None

    @model_validator(mode="after")
    def validate_frame_anchors(self) -> "FrameAnchoredStoryboard":
        if len(self.scenes) < 2:
            raise ValueError("frame-anchored storyboard requires at least two scenes")
        if self.scenes[0].frame_anchor != "first_frame":
            raise ValueError("first scene must use first_frame anchor")
        if self.scenes[-1].frame_anchor != "last_frame":
            raise ValueError("last scene must use last_frame anchor")
        if any(scene.frame_anchor != "transition" for scene in self.scenes[1:-1]):
            raise ValueError("middle scenes must use transition anchor")
        return self


def _scene_director_beat_ids(
    scene: FrameAnchoredStoryboardScene,
    *,
    valid_beat_ids: set[str] | None = None,
) -> set[str]:
    beat_ids = {beat_id.strip() for beat_id in scene.cinematic_beats if beat_id.strip()}
    legacy_beat_id = (scene.cinematic_beat or "").strip()
    if legacy_beat_id:
        beat_ids.add(legacy_beat_id)
    if valid_beat_ids is not None:
        beat_ids.intersection_update(valid_beat_ids)
    return beat_ids


def _scene_can_carry_director_beat(scene: FrameAnchoredStoryboardScene) -> bool:
    has_timed_window = (
        scene.start_second is not None
        and scene.end_second is not None
        and scene.end_second > scene.start_second
    )
    has_climax_direction = any(
        (value or "").strip()
        for value in (
            scene.camera_instruction,
            scene.action_result_requirement,
            scene.effect_timing,
            scene.anti_flattening_requirement,
        )
    )
    return (
        has_timed_window
        or scene.frame_anchor == "transition"
        or scene.tension_stage == "climax"
        or has_climax_direction
    )


def _assign_missing_director_beat_ids(
    storyboard: FrameAnchoredStoryboard,
    plan: FrameAnchoredDirectorPlan,
) -> None:
    """Link core beats by target-time overlap without changing scene creative direction."""
    valid_beat_ids = {beat.beat_id for beat in plan.climax_beats}
    assigned_beat_ids = set().union(
        *(
            _scene_director_beat_ids(scene, valid_beat_ids=valid_beat_ids)
            for scene in storyboard.scenes
        )
    )
    candidate_scenes = [
        scene for scene in storyboard.scenes if _scene_can_carry_director_beat(scene)
    ]
    if not candidate_scenes:
        return

    duration = float(storyboard.duration_seconds)
    for beat in plan.climax_beats:
        if beat.importance != "core" or beat.beat_id in assigned_beat_ids:
            continue

        beat_start = beat.start_ratio * duration
        beat_end = beat.end_ratio * duration
        beat_midpoint = (beat_start + beat_end) / 2

        def scene_score(
            scene: FrameAnchoredStoryboardScene,
            *,
            _beat_start: float = beat_start,
            _beat_end: float = beat_end,
            _beat_midpoint: float = beat_midpoint,
        ) -> tuple[float, float, int]:
            scene_start = 0.0 if scene.start_second is None else scene.start_second
            scene_end = duration if scene.end_second is None else scene.end_second
            overlap = max(
                0.0,
                min(scene_end, _beat_end) - max(scene_start, _beat_start),
            )
            scene_midpoint = (scene_start + scene_end) / 2
            return (
                overlap,
                -abs(scene_midpoint - _beat_midpoint),
                1 if scene.frame_anchor == "transition" else 0,
            )

        scene = max(candidate_scenes, key=scene_score)
        if beat.beat_id not in scene.cinematic_beats:
            scene.cinematic_beats.append(beat.beat_id)
        if not (scene.cinematic_beat or "").strip():
            scene.cinematic_beat = beat.beat_id
        assigned_beat_ids.add(beat.beat_id)


def validate_director_coverage(
    storyboard: FrameAnchoredStoryboard,
    plan: FrameAnchoredDirectorPlan | None,
) -> None:
    if plan is None:
        return
    _assign_missing_director_beat_ids(storyboard, plan)
    valid_beat_ids = {beat.beat_id for beat in plan.climax_beats}
    covered_beat_ids = set().union(
        *(
            _scene_director_beat_ids(scene, valid_beat_ids=valid_beat_ids)
            for scene in storyboard.scenes
        )
    )
    for beat in plan.climax_beats:
        if beat.importance != "core":
            continue
        if beat.beat_id not in covered_beat_ids:
            raise ValueError(f"storyboard is missing required director beat: {beat.beat_id}")


class GeneratedImage(BaseModel):
    prompt: str
    url: str | None = None
    storage_key: str | None = None
    alt_text: str | None = None
    size: str = "1:1"
    metadata: dict = Field(default_factory=dict)
