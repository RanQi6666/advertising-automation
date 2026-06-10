from datetime import date
from decimal import Decimal

from sqlalchemy import JSON, Date, ForeignKey, Integer, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default


class InsightDaily(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "insights_daily"

    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id"), index=True)
    publish_job_id: Mapped[str | None] = mapped_column(
        ForeignKey("publish_jobs.id"), nullable=True, index=True
    )
    metric_date: Mapped[date] = mapped_column(Date, index=True)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    clicks: Mapped[int] = mapped_column(Integer, default=0)
    spend: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0)
    ctr: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    cpc: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    conversions: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    campaign = relationship("Campaign", back_populates="insights")
