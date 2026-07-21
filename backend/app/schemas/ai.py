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


class FrameAnchoredDirectorPlan(BaseModel):
    narrative_objective: str = Field(min_length=1)
    attention_path: list[str] = Field(min_length=1)
    tension_curve: list[DirectorTensionStage] = Field(min_length=1)
    climax_beats: list[DirectorBeat] = Field(min_length=1)
    overlay_lifecycle_plan: list[DirectorOverlayInstruction] = Field(default_factory=list)
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
        for beat in self.climax_beats:
            if beat.stage != "climax":
                raise ValueError("director climax beats must use climax stage")
            if not beat.source_evidence:
                raise ValueError("climax beat requires source evidence")
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
    camera_instruction: str | None = None
    tension_stage: DirectorTensionStage | None = None
    action_result_requirement: str | None = None
    effect_timing: str | None = None
    overlay_instruction: DirectorOverlayInstruction | None = None
    anti_flattening_requirement: str | None = None


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


def validate_director_coverage(
    storyboard: FrameAnchoredStoryboard,
    plan: FrameAnchoredDirectorPlan | None,
) -> None:
    if plan is None:
        return
    for beat in plan.climax_beats:
        if beat.importance != "core":
            continue
        if not any(scene.cinematic_beat == beat.beat_id for scene in storyboard.scenes):
            raise ValueError(f"storyboard is missing required director beat: {beat.beat_id}")


class GeneratedImage(BaseModel):
    prompt: str
    url: str | None = None
    storage_key: str | None = None
    alt_text: str | None = None
    size: str = "1:1"
    metadata: dict = Field(default_factory=dict)
