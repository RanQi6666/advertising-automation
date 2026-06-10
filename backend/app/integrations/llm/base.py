from typing import Protocol

from backend.app.db.models.campaign import Campaign
from backend.app.db.models.copy_draft import CopyDraft
from backend.app.db.models.topic import ContentTopic
from backend.app.schemas.ai import CopyDraftCandidate, ImageBrief, TopicCandidate


class LLMProvider(Protocol):
    async def generate_topics(
        self,
        campaign: Campaign,
        limit: int,
        signals: dict,
    ) -> list[TopicCandidate]:
        """Generate selectable content topics."""

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
    ) -> list[ImageBrief]:
        """Condense copy into image-by-image creative briefs."""
