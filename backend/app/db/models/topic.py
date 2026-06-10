from sqlalchemy import JSON, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    json_default,
    list_default,
)
from backend.app.db.models.enums import TopicStatus


class ContentTopic(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "content_topics"

    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    angle: Mapped[str] = mapped_column(Text)
    audience: Mapped[str | None] = mapped_column(Text, nullable=True)
    selling_points: Mapped[list] = mapped_column(JSON, default=list_default)
    risk_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=TopicStatus.PROPOSED.value, index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_data: Mapped[dict] = mapped_column(JSON, default=json_default)

    campaign = relationship("Campaign", back_populates="topics")
    copy_drafts = relationship("CopyDraft", back_populates="topic")
