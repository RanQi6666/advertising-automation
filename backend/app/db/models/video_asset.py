from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    json_default,
    list_default,
)
from backend.app.db.models.enums import VideoStatus


class VideoAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "video_assets"

    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    draft_id: Mapped[str | None] = mapped_column(
        ForeignKey("copy_drafts.id"),
        nullable=True,
        index=True,
    )
    source_asset_ids: Mapped[list] = mapped_column(JSON, default=list_default)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    storyboard: Mapped[list] = mapped_column(JSON, default=list_default)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    aspect_ratio: Mapped[str] = mapped_column(String(32), default="9:16")
    status: Mapped[str] = mapped_column(
        String(32),
        default=VideoStatus.REQUESTED.value,
        index=True,
    )
    provider_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    campaign = relationship("Campaign", back_populates="video_assets")
    draft = relationship("CopyDraft")
