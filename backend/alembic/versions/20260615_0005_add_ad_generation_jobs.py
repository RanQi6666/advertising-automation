"""add ad generation jobs

Revision ID: 20260615_0005
Revises: 20260610_0004
Create Date: 2026-06-15 00:05:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260615_0005"
down_revision: str | None = "20260610_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ad_generation_jobs",
        sa.Column("external_order_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("callback_url", sa.Text(), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("result_payload", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ad_generation_jobs")),
    )
    op.create_index(
        op.f("ix_ad_generation_jobs_external_order_id"),
        "ad_generation_jobs",
        ["external_order_id"],
    )
    op.create_index(
        op.f("ix_ad_generation_jobs_status"),
        "ad_generation_jobs",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ad_generation_jobs_status"), table_name="ad_generation_jobs")
    op.drop_index(
        op.f("ix_ad_generation_jobs_external_order_id"),
        table_name="ad_generation_jobs",
    )
    op.drop_table("ad_generation_jobs")
