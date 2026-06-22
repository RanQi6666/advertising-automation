from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default


class AdPerformanceAnalysis(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ad_performance_analyses"

    external_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    source_type: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    campaign_external_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    campaign_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adset_external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    adset_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    creative_external_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    creative_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    date_start: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    date_stop: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    request_payload: Mapped[dict] = mapped_column(JSON, default=json_default)
    metrics: Mapped[dict] = mapped_column(JSON, default=json_default)
    analysis_result: Mapped[dict] = mapped_column(JSON, default=json_default)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
