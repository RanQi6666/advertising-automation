from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, json_default


class FacebookAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "facebook_accounts"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    page_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ad_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    access_token_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    client = relationship("Client", back_populates="facebook_accounts")
