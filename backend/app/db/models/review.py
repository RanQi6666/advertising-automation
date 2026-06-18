from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default
from backend.app.db.models.enums import ReviewStatus


class ReviewTask(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "review_tasks"

    campaign_id: Mapped[str | None] = mapped_column(
        ForeignKey("campaigns.id"), nullable=True, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[str] = mapped_column(String(36), index=True)
    reviewer_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default=ReviewStatus.PENDING.value, index=True)
    decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    reviewer = relationship("User", back_populates="reviews")
