from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default
from backend.app.db.models.enums import CampaignStatus


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "campaigns"

    client_id: Mapped[str | None] = mapped_column(
        ForeignKey("clients.id"),
        nullable=True,
        index=True,
    )
    brand_id: Mapped[str | None] = mapped_column(ForeignKey("brands.id"), nullable=True, index=True)
    work_order_id: Mapped[str | None] = mapped_column(
        ForeignKey("work_orders.id"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), index=True)
    objective: Mapped[str | None] = mapped_column(String(128), nullable=True)
    product_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    audience_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=CampaignStatus.ACTIVE.value, index=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    client = relationship("Client", back_populates="campaigns")
    brand = relationship("Brand", back_populates="campaigns")
    work_order = relationship("WorkOrder", back_populates="campaigns")
    landing_page_snapshots = relationship(
        "LandingPageSnapshot",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    topics = relationship("ContentTopic", back_populates="campaign", cascade="all, delete-orphan")
    copy_drafts = relationship("CopyDraft", back_populates="campaign", cascade="all, delete-orphan")
    creative_assets = relationship(
        "CreativeAsset",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    video_assets = relationship(
        "VideoAsset",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )
    publish_jobs = relationship(
        "PublishJob", back_populates="campaign", cascade="all, delete-orphan"
    )
    insights = relationship("InsightDaily", back_populates="campaign", cascade="all, delete-orphan")
