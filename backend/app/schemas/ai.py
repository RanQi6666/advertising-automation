from pydantic import BaseModel, Field


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


class GeneratedImage(BaseModel):
    prompt: str
    url: str | None = None
    storage_key: str | None = None
    alt_text: str | None = None
    size: str = "1:1"
    metadata: dict = Field(default_factory=dict)
