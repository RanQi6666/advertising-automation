from enum import StrEnum


class CampaignStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class TopicStatus(StrEnum):
    PROPOSED = "proposed"
    SELECTED = "selected"
    REJECTED = "rejected"
    ARCHIVED = "archived"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_REVISION = "needs_revision"
    APPROVED = "approved"
    REJECTED = "rejected"


class CreativeStatus(StrEnum):
    GENERATED = "generated"
    NEEDS_REVISION = "needs_revision"
    APPROVED = "approved"
    REJECTED = "rejected"


class VideoStatus(StrEnum):
    REQUESTED = "requested"
    GENERATING = "generating"
    GENERATED = "generated"
    NEEDS_REVISION = "needs_revision"
    APPROVED = "approved"
    REJECTED = "rejected"
    FAILED = "failed"


class ReviewStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_REVISION = "needs_revision"


class ReviewEntityType(StrEnum):
    TOPIC = "topic"
    COPY_DRAFT = "copy_draft"
    CREATIVE_ASSET = "creative_asset"
    VIDEO_ASSET = "video_asset"
