from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default


class AdAnalysisReferenceAd(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Public similar-ad reference captured for an ad-performance analysis job.

    These rows intentionally store public/proxy evidence only. They must not be
    treated as verified Meta delivery performance.
    """

    __tablename__ = "ad_analysis_reference_ads"

    analysis_record_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("ad_performance_analyses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reference_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_domain: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    performance_evidence_json: Mapped[dict] = mapped_column(JSON, default=json_default)
    creative_analysis_json: Mapped[dict] = mapped_column(JSON, default=json_default)
    raw_excerpt_json: Mapped[dict] = mapped_column(JSON, default=json_default)

    analysis = relationship("AdPerformanceAnalysis", back_populates="reference_ads")
