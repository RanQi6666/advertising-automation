from sqlalchemy import JSON, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.db.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    json_default,
    list_default,
)


class Brand(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "brands"

    client_id: Mapped[str] = mapped_column(ForeignKey("clients.id"), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    voice: Mapped[str | None] = mapped_column(Text, nullable=True)
    guidelines: Mapped[str | None] = mapped_column(Text, nullable=True)
    banned_words: Mapped[list] = mapped_column(JSON, default=list_default)
    compliance_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=json_default)

    client = relationship("Client", back_populates="brands")
    campaigns = relationship("Campaign", back_populates="brand")
