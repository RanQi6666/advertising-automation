"""add video assets

Revision ID: 20260610_0002
Revises: 20260610_0001
Create Date: 2026-06-10 00:10:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260610_0002"
down_revision: str | None = "20260610_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "video_assets",
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=True),
        sa.Column("source_asset_ids", sa.JSON(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=True),
        sa.Column("storyboard", sa.JSON(), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("aspect_ratio", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_job_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_video_assets_campaign_id_campaigns"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["copy_drafts.id"],
            name=op.f("fk_video_assets_draft_id_copy_drafts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_video_assets")),
    )
    op.create_index(op.f("ix_video_assets_campaign_id"), "video_assets", ["campaign_id"])
    op.create_index(op.f("ix_video_assets_draft_id"), "video_assets", ["draft_id"])
    op.create_index(
        op.f("ix_video_assets_provider_job_id"),
        "video_assets",
        ["provider_job_id"],
    )
    op.create_index(op.f("ix_video_assets_status"), "video_assets", ["status"])


def downgrade() -> None:
    op.drop_index(op.f("ix_video_assets_status"), table_name="video_assets")
    op.drop_index(op.f("ix_video_assets_provider_job_id"), table_name="video_assets")
    op.drop_index(op.f("ix_video_assets_draft_id"), table_name="video_assets")
    op.drop_index(op.f("ix_video_assets_campaign_id"), table_name="video_assets")
    op.drop_table("video_assets")
