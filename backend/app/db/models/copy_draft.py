from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default
from backend.app.db.models.enums import DraftStatus


class CopyDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "copy_drafts"

    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    topic_id: Mapped[str] = mapped_column(ForeignKey("content_topics.id"), index=True)
    body: Mapped[str] = mapped_column(Text)
    primary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    headline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cta: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=DraftStatus.DRAFT.value, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    campaign = relationship("Campaign", back_populates="copy_drafts")
    topic = relationship("ContentTopic", back_populates="copy_drafts")
    creative_assets = relationship("CreativeAsset", back_populates="draft")
