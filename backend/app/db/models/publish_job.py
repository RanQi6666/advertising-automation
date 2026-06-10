from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default
from backend.app.db.models.enums import PublishStatus


class PublishJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "publish_jobs"

    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    draft_id: Mapped[str | None] = mapped_column(
        ForeignKey("copy_drafts.id"), nullable=True, index=True
    )
    channel: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=json_default)
    status: Mapped[str] = mapped_column(String(32), default=PublishStatus.QUEUED.value, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    campaign = relationship("Campaign", back_populates="publish_jobs")
