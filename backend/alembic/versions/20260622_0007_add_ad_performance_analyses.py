"""add ad performance analyses

Revision ID: 20260622_0007
Revises: 20260615_0006
Create Date: 2026-06-22 00:07:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260622_0007"
down_revision: str | None = "20260615_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ad_performance_analyses",
        sa.Column("external_user_id", sa.String(length=128), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("campaign_external_id", sa.String(length=128), nullable=True),
        sa.Column("campaign_name", sa.String(length=255), nullable=True),
        sa.Column("adset_external_id", sa.String(length=128), nullable=True),
        sa.Column("adset_name", sa.String(length=255), nullable=True),
        sa.Column("creative_external_id", sa.String(length=128), nullable=True),
        sa.Column("creative_name", sa.String(length=255), nullable=True),
        sa.Column("date_start", sa.String(length=32), nullable=True),
        sa.Column("date_stop", sa.String(length=32), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("analysis_result", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ad_performance_analyses")),
    )
    op.create_index(
        op.f("ix_ad_performance_analyses_adset_external_id"),
        "ad_performance_analyses",
        ["adset_external_id"],
    )
    op.create_index(
        op.f("ix_ad_performance_analyses_campaign_external_id"),
        "ad_performance_analyses",
        ["campaign_external_id"],
    )
    op.create_index(
        op.f("ix_ad_performance_analyses_creative_external_id"),
        "ad_performance_analyses",
        ["creative_external_id"],
    )
    op.create_index(
        op.f("ix_ad_performance_analyses_date_start"),
        "ad_performance_analyses",
        ["date_start"],
    )
    op.create_index(
        op.f("ix_ad_performance_analyses_date_stop"),
        "ad_performance_analyses",
        ["date_stop"],
    )
    op.create_index(
        op.f("ix_ad_performance_analyses_external_user_id"),
        "ad_performance_analyses",
        ["external_user_id"],
    )
    op.create_index(
        op.f("ix_ad_performance_analyses_source_type"),
        "ad_performance_analyses",
        ["source_type"],
    )
    op.create_index(
        op.f("ix_ad_performance_analyses_status"),
        "ad_performance_analyses",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ad_performance_analyses_status"), table_name="ad_performance_analyses")
    op.drop_index(
        op.f("ix_ad_performance_analyses_source_type"), table_name="ad_performance_analyses"
    )
    op.drop_index(
        op.f("ix_ad_performance_analyses_external_user_id"),
        table_name="ad_performance_analyses",
    )
    op.drop_index(
        op.f("ix_ad_performance_analyses_date_stop"), table_name="ad_performance_analyses"
    )
    op.drop_index(
        op.f("ix_ad_performance_analyses_date_start"), table_name="ad_performance_analyses"
    )
    op.drop_index(
        op.f("ix_ad_performance_analyses_creative_external_id"),
        table_name="ad_performance_analyses",
    )
    op.drop_index(
        op.f("ix_ad_performance_analyses_campaign_external_id"),
        table_name="ad_performance_analyses",
    )
    op.drop_index(
        op.f("ix_ad_performance_analyses_adset_external_id"),
        table_name="ad_performance_analyses",
    )
    op.drop_table("ad_performance_analyses")
