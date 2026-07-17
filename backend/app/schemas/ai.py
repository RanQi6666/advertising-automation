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


class FrameAnalysis(BaseModel):
    first_frame: FrameVisualFacts
    last_frame: FrameVisualFacts
    transition_brief: FrameTransitionBrief
    language_analysis: FrameLanguageAnalysis


class StoryboardSoundDesign(BaseModel):
    music: str | None = None
    ambience: str | None = None


class FrameAnchoredStoryboardScene(BaseModel):
    scene_index: int
    start_second: int | None = None
    end_second: int | None = None
    frame_anchor: Literal["first_frame", "transition", "last_frame"]
    visual: str
    motion: str | None = None
    transition_goal: str | None = None
    subtitle: str | None = None
    voiceover: str | None = None
    sound_effects: list[str] = Field(default_factory=list)
    notes: str | None = None


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


class GeneratedImage(BaseModel):
    prompt: str
    url: str | None = None
    storage_key: str | None = None
    alt_text: str | None = None
    size: str = "1:1"
    metadata: dict = Field(default_factory=dict)
