from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default


class WorkOrder(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "work_orders"

    raw_content: Mapped[str] = mapped_column(Text)
    parsed_fields: Mapped[dict] = mapped_column(JSON, default=json_default)
    project_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    country: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    media: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    event_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audience_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    landing_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    report_timezone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="received", index=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    campaigns = relationship("Campaign", back_populates="work_order")
