"""add landing page snapshots

Revision ID: 20260610_0004
Revises: 20260610_0003
Create Date: 2026-06-10 00:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260610_0004"
down_revision: str | None = "20260610_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "landing_page_snapshots",
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("work_order_id", sa.String(length=36), nullable=True),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=512), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("extracted_data", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_landing_page_snapshots_campaign_id_campaigns"),
        ),
        sa.ForeignKeyConstraint(
            ["work_order_id"],
            ["work_orders.id"],
            name=op.f("fk_landing_page_snapshots_work_order_id_work_orders"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_landing_page_snapshots")),
    )
    op.create_index(
        op.f("ix_landing_page_snapshots_campaign_id"),
        "landing_page_snapshots",
        ["campaign_id"],
    )
    op.create_index(
        op.f("ix_landing_page_snapshots_status"),
        "landing_page_snapshots",
        ["status"],
    )
    op.create_index(
        op.f("ix_landing_page_snapshots_work_order_id"),
        "landing_page_snapshots",
        ["work_order_id"],
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_landing_page_snapshots_work_order_id"),
        table_name="landing_page_snapshots",
    )
    op.drop_index(
        op.f("ix_landing_page_snapshots_status"),
        table_name="landing_page_snapshots",
    )
    op.drop_index(
        op.f("ix_landing_page_snapshots_campaign_id"),
        table_name="landing_page_snapshots",
    )
    op.drop_table("landing_page_snapshots")
