"""add generation task owner

Revision ID: 20260702_0011
Revises: 20260701_0010
Create Date: 2026-07-02 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260702_0011"
down_revision: str | None = "20260701_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "generation_tasks",
        sa.Column("owner_user_id", sa.String(length=36), nullable=True),
    )
    op.create_index(
        op.f("ix_generation_tasks_owner_user_id"),
        "generation_tasks",
        ["owner_user_id"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_generation_tasks_owner_user_id"), table_name="generation_tasks")
    op.drop_column("generation_tasks", "owner_user_id")
