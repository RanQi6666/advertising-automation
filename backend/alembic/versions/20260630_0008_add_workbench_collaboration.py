"""add workbench collaboration fields

Revision ID: 20260630_0008
Revises: 20260622_0007
Create Date: 2026-06-30 00:08:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260630_0008"
down_revision: str | None = "20260622_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _add_collaboration_columns("ad_generation_jobs")
    _add_collaboration_columns("ad_performance_analyses")


def downgrade() -> None:
    _drop_collaboration_columns("ad_performance_analyses")
    _drop_collaboration_columns("ad_generation_jobs")


def _add_collaboration_columns(table_name: str) -> None:
    op.add_column(table_name, sa.Column("owner_user_id", sa.String(length=36), nullable=True))
    op.add_column(table_name, sa.Column("locked_by", sa.String(length=36), nullable=True))
    op.add_column(table_name, sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f(f"ix_{table_name}_owner_user_id"), table_name, ["owner_user_id"])
    op.create_index(op.f(f"ix_{table_name}_locked_by"), table_name, ["locked_by"])


def _drop_collaboration_columns(table_name: str) -> None:
    op.drop_index(op.f(f"ix_{table_name}_locked_by"), table_name=table_name)
    op.drop_index(op.f(f"ix_{table_name}_owner_user_id"), table_name=table_name)
    op.drop_column(table_name, "locked_at")
    op.drop_column(table_name, "locked_by")
    op.drop_column(table_name, "owner_user_id")
