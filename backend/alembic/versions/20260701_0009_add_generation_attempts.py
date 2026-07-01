"""add generation attempts

Revision ID: 20260701_0009
Revises: 20260630_0008
Create Date: 2026-07-01 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0009"
down_revision: str | None = "20260630_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("business_type", sa.String(length=64), nullable=False),
        sa.Column("business_id", sa.String(length=128), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=True),
        sa.Column("campaign_id", sa.String(length=36), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failed_count", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_attempts")),
    )
    op.create_index(
        op.f("ix_generation_attempts_business_type"),
        "generation_attempts",
        ["business_type"],
    )
    op.create_index(
        op.f("ix_generation_attempts_business_id"),
        "generation_attempts",
        ["business_id"],
    )
    op.create_index(op.f("ix_generation_attempts_job_id"), "generation_attempts", ["job_id"])
    op.create_index(
        op.f("ix_generation_attempts_campaign_id"),
        "generation_attempts",
        ["campaign_id"],
    )
    op.create_index(op.f("ix_generation_attempts_stage"), "generation_attempts", ["stage"])
    op.create_index(op.f("ix_generation_attempts_status"), "generation_attempts", ["status"])
    op.create_index(
        op.f("ix_generation_attempts_error_code"),
        "generation_attempts",
        ["error_code"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_generation_attempts_error_code"), table_name="generation_attempts")
    op.drop_index(op.f("ix_generation_attempts_status"), table_name="generation_attempts")
    op.drop_index(op.f("ix_generation_attempts_stage"), table_name="generation_attempts")
    op.drop_index(op.f("ix_generation_attempts_campaign_id"), table_name="generation_attempts")
    op.drop_index(op.f("ix_generation_attempts_job_id"), table_name="generation_attempts")
    op.drop_index(op.f("ix_generation_attempts_business_id"), table_name="generation_attempts")
    op.drop_index(op.f("ix_generation_attempts_business_type"), table_name="generation_attempts")
    op.drop_table("generation_attempts")
