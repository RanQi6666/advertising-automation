"""add generation tasks

Revision ID: 20260701_0010
Revises: 20260701_0009
Create Date: 2026-07-01 01:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260701_0010"
down_revision: str | None = "20260701_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "generation_tasks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("queue_name", sa.String(length=64), nullable=False),
        sa.Column("task_type", sa.String(length=64), nullable=False),
        sa.Column("business_type", sa.String(length=64), nullable=False),
        sa.Column("business_id", sa.String(length=128), nullable=False),
        sa.Column("campaign_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_generation_tasks")),
    )
    op.create_index(op.f("ix_generation_tasks_queue_name"), "generation_tasks", ["queue_name"])
    op.create_index(op.f("ix_generation_tasks_task_type"), "generation_tasks", ["task_type"])
    op.create_index(
        op.f("ix_generation_tasks_business_type"), "generation_tasks", ["business_type"]
    )
    op.create_index(op.f("ix_generation_tasks_business_id"), "generation_tasks", ["business_id"])
    op.create_index(op.f("ix_generation_tasks_campaign_id"), "generation_tasks", ["campaign_id"])
    op.create_index(op.f("ix_generation_tasks_status"), "generation_tasks", ["status"])
    op.create_index(op.f("ix_generation_tasks_priority"), "generation_tasks", ["priority"])
    op.create_index(op.f("ix_generation_tasks_error_code"), "generation_tasks", ["error_code"])


def downgrade() -> None:
    op.drop_index(op.f("ix_generation_tasks_error_code"), table_name="generation_tasks")
    op.drop_index(op.f("ix_generation_tasks_priority"), table_name="generation_tasks")
    op.drop_index(op.f("ix_generation_tasks_status"), table_name="generation_tasks")
    op.drop_index(op.f("ix_generation_tasks_campaign_id"), table_name="generation_tasks")
    op.drop_index(op.f("ix_generation_tasks_business_id"), table_name="generation_tasks")
    op.drop_index(op.f("ix_generation_tasks_business_type"), table_name="generation_tasks")
    op.drop_index(op.f("ix_generation_tasks_task_type"), table_name="generation_tasks")
    op.drop_index(op.f("ix_generation_tasks_queue_name"), table_name="generation_tasks")
    op.drop_table("generation_tasks")
