"""add work orders

Revision ID: 20260610_0003
Revises: 20260610_0002
Create Date: 2026-06-10 00:20:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260610_0003"
down_revision: str | None = "20260610_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_orders",
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("parsed_fields", sa.JSON(), nullable=False),
        sa.Column("project_name", sa.String(length=255), nullable=True),
        sa.Column("country", sa.String(length=128), nullable=True),
        sa.Column("media", sa.String(length=128), nullable=True),
        sa.Column("event_name", sa.String(length=128), nullable=True),
        sa.Column("product_name", sa.String(length=255), nullable=True),
        sa.Column("audience_description", sa.Text(), nullable=True),
        sa.Column("landing_url", sa.Text(), nullable=True),
        sa.Column("report_timezone", sa.String(length=32), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_orders")),
    )
    op.create_index(op.f("ix_work_orders_country"), "work_orders", ["country"])
    op.create_index(op.f("ix_work_orders_media"), "work_orders", ["media"])
    op.create_index(op.f("ix_work_orders_project_name"), "work_orders", ["project_name"])
    op.create_index(op.f("ix_work_orders_status"), "work_orders", ["status"])

    op.alter_column("campaigns", "client_id", existing_type=sa.String(length=36), nullable=True)
    op.add_column("campaigns", sa.Column("work_order_id", sa.String(length=36), nullable=True))
    op.create_index(op.f("ix_campaigns_work_order_id"), "campaigns", ["work_order_id"])
    op.create_foreign_key(
        op.f("fk_campaigns_work_order_id_work_orders"),
        "campaigns",
        "work_orders",
        ["work_order_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_campaigns_work_order_id_work_orders"),
        "campaigns",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_campaigns_work_order_id"), table_name="campaigns")
    op.drop_column("campaigns", "work_order_id")
    op.alter_column("campaigns", "client_id", existing_type=sa.String(length=36), nullable=False)

    op.drop_index(op.f("ix_work_orders_status"), table_name="work_orders")
    op.drop_index(op.f("ix_work_orders_project_name"), table_name="work_orders")
    op.drop_index(op.f("ix_work_orders_media"), table_name="work_orders")
    op.drop_index(op.f("ix_work_orders_country"), table_name="work_orders")
    op.drop_table("work_orders")
