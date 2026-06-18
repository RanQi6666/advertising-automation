from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default, utcnow


class LandingPageSnapshot(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "landing_page_snapshots"

    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    work_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_orders.id"),
        nullable=True,
        index=True,
    )
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="fetched", index=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_data: Mapped[dict] = mapped_column(JSON, default=json_default)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    campaign = relationship("Campaign", back_populates="landing_page_snapshots")
