from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, json_default, utcnow


def new_ad_research_job_id() -> str:
    return f"adr_{uuid4().hex}"


class AdResearchJob(TimestampMixin, Base):
    """One externally pollable public-ad research request.

    The persisted result contains only public ad metadata and is removed after its
    short retention window. ``external_user_id`` is intentionally nullable so an
    expired request can release its idempotency key for a future logical request.
    """

    __tablename__ = "ad_research_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=new_ad_research_job_id)
    external_user_id: Mapped[str | None] = mapped_column(
        String(128), nullable=True, unique=True, index=True
    )
    request_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    country: Mapped[str] = mapped_column(String(8), index=True)
    category: Mapped[str] = mapped_column(String(128), index=True)
    seed_keywords_json: Mapped[list[str]] = mapped_column("seed_keywords", JSON, default=list)
    target_count: Mapped[int] = mapped_column(Integer, default=25)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    current_round: Mapped[int] = mapped_column(Integer, default=0)
    progress_json: Mapped[dict] = mapped_column("progress", JSON, default=json_default)
    summary_json: Mapped[dict] = mapped_column("summary", JSON, default=json_default)
    result_json: Mapped[dict | None] = mapped_column("result", JSON, nullable=True)
    result_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    generation_task_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("generation_tasks.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    error_message_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    generation_task = relationship("GenerationTask", foreign_keys=[generation_task_id])

    @property
    def is_result_expired(self) -> bool:
        return self.result_expires_at is not None and self.result_expires_at <= utcnow()
