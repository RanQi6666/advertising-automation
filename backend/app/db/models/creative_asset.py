from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default
from backend.app.db.models.enums import CreativeStatus


class CreativeAsset(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "creative_assets"

    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("copy_drafts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(64), default="image")
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    prompt: Mapped[str] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    size: Mapped[str] = mapped_column(String(32), default="1:1")
    status: Mapped[str] = mapped_column(
        String(32), default=CreativeStatus.GENERATED.value, index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    campaign = relationship("Campaign", back_populates="creative_assets")
    draft = relationship("CopyDraft", back_populates="creative_assets")
