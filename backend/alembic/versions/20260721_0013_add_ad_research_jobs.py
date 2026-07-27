"""add ad research jobs

Revision ID: 20260721_0013
Revises: 20260713_0012
Create Date: 2026-07-21 09:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260721_0013"
down_revision: str | None = "20260713_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ad_research_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("external_user_id", sa.String(length=128), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("country", sa.String(length=8), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=False),
        sa.Column("seed_keywords", sa.JSON(), nullable=False),
        sa.Column("target_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("current_round", sa.Integer(), nullable=False),
        sa.Column("progress", sa.JSON(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("result_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation_task_id", sa.String(length=36), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message_summary", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["generation_task_id"], ["generation_tasks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_user_id"),
        sa.UniqueConstraint("generation_task_id"),
    )
    index_names = (
        "external_user_id",
        "request_fingerprint",
        "country",
        "category",
        "status",
        "error_code",
    )
    for name in index_names:
        op.create_index(op.f(f"ix_ad_research_jobs_{name}"), "ad_research_jobs", [name])
    op.create_index(
        "ix_ad_research_jobs_status_result_expires_at",
        "ad_research_jobs",
        ["status", "result_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ad_research_jobs_status_result_expires_at",
        table_name="ad_research_jobs",
    )
    index_names = (
        "error_code",
        "status",
        "category",
        "country",
        "request_fingerprint",
        "external_user_id",
    )
    for name in index_names:
        op.drop_index(op.f(f"ix_ad_research_jobs_{name}"), table_name="ad_research_jobs")
    op.drop_table("ad_research_jobs")
