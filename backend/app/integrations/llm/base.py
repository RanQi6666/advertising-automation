from collections.abc import AsyncIterator
from typing import Protocol

from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.creative_asset import CreativeAsset
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.ai import (
    CopyDraftCandidate,
    DirectorActionCorrection,
    FrameAnalysis,
    FrameAnchoredDirectorPlan,
    FrameAnchoredStoryboard,
    ImageBrief,
    ReferenceVideoFrame,
    TopicCandidate,
    VideoStoryboardCandidate,
)


class LLMProvider(Protocol):
    async def extract_delivery_fields(self, raw_content: str) -> dict:
        """Extract and suggest work-order fields that directly affect ad delivery."""

    async def analyze_ad_performance(self, context: dict) -> dict:
        """Analyze ad performance data and return operator-facing optimization advice."""

    def stream_ad_performance_analysis(self, context: dict) -> AsyncIterator[dict]:
        """Stream ad performance analysis text and yield the final normalized result."""

    async def generate_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> list[TopicCandidate]:
        """Generate selectable content topics."""

    def stream_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> AsyncIterator[TopicCandidate]:
        """Generate selectable content topics incrementally from one provider request."""

    async def generate_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        constraints: dict,
    ) -> CopyDraftCandidate:
        """Generate ad copy for a selected topic."""

    async def revise_copy(
        self,
        campaign: Campaign,
        topic: ContentTopic,
        draft: CopyDraft,
        feedback: str,
        constraints: dict,
    ) -> CopyDraftCandidate:
        """Revise existing copy using human feedback."""

    async def generate_image_briefs(
        self,
        draft: CopyDraft,
        count: int,
        size: str,
        feedback: str | None = None,
        source_asset: CreativeAsset | None = None,
        storyboard_context: dict | None = None,
    ) -> list[ImageBrief]:
        """Condense copy into image-by-image creative briefs."""

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
        """Create a scene-by-scene video storyboard from copy and image assets."""

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
        """Analyze exact endpoint images and optional chronological reference frames."""

    async def direct_frame_anchored_video_storyboard(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        frame_analysis: FrameAnalysis,
        duration_seconds: int,
        aspect_ratio: str,
    ) -> FrameAnchoredDirectorPlan:
        """Create a private evidence-backed cinematic director plan for one generated clip."""

    async def generate_frame_anchored_video_storyboard(
        self,
        first_frame_image_url: str,
        last_frame_image_url: str,
        frame_analysis: FrameAnalysis,
        duration_seconds: int,
        aspect_ratio: str,
        director_corrections: list[DirectorActionCorrection] | None = None,
    ) -> FrameAnchoredStoryboard:
        """Create an anchored storyboard using the same first and last frames."""

    def stream_video_storyboard_text(
        self,
        campaign: Campaign,
        draft: CopyDraft | None,
        assets: list[CreativeAsset],
        duration_seconds: int,
        aspect_ratio: str,
        context: dict,
        instructions: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream an editable plain-text video storyboard script."""

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
        """Revise an existing video storyboard using human feedback."""

    def stream_video_storyboard_revision_text(
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
        """Stream a revised editable plain-text video storyboard script."""
