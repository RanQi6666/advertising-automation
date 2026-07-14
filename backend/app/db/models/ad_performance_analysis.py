from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    locked_by: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Async external ad-performance analysis job fields. The existing synchronous
    # /integrations/ad-performance/analyses endpoint keeps using the legacy fields above.
    analysis_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    external_request_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )
    payload_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    normalized_payload: Mapped[dict] = mapped_column(JSON, default=json_default)
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    progress: Mapped[int | None] = mapped_column(Integer, nullable=True)
    analysis_scope: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    result_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    generation_task_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("generation_tasks.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    media_summary: Mapped[dict] = mapped_column(JSON, default=json_default)
    research_summary: Mapped[dict] = mapped_column(JSON, default=json_default)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_dispatched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dispatch_claimed_by: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    dispatch_claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dispatch_attempts: Mapped[int] = mapped_column(Integer, default=0)
    dispatch_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error_retryable: Mapped[bool] = mapped_column(Boolean, default=False)

    generation_task = relationship("GenerationTask", foreign_keys=[generation_task_id])
    reference_ads = relationship(
        "AdAnalysisReferenceAd",
        back_populates="analysis",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
