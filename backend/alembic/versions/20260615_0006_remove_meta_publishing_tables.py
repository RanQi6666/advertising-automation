"""remove meta publishing tables

Revision ID: 20260615_0006
Revises: 20260615_0005
Create Date: 2026-06-15 00:06:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260615_0006"
down_revision: str | None = "20260615_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("insights_daily"):
        op.drop_table("insights_daily")
    if _has_table("publish_jobs"):
        op.drop_table("publish_jobs")
    if _has_table("facebook_accounts"):
        op.drop_table("facebook_accounts")


def downgrade() -> None:
    op.create_table(
        "facebook_accounts",
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("page_id", sa.String(length=128), nullable=True),
        sa.Column("ad_account_id", sa.String(length=128), nullable=True),
        sa.Column("access_token_ref", sa.String(length=255), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["clients.id"],
            name=op.f("fk_facebook_accounts_client_id_clients"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_facebook_accounts")),
    )
    op.create_index(
        op.f("ix_facebook_accounts_ad_account_id"), "facebook_accounts", ["ad_account_id"]
    )
    op.create_index(op.f("ix_facebook_accounts_client_id"), "facebook_accounts", ["client_id"])
    op.create_index(op.f("ix_facebook_accounts_page_id"), "facebook_accounts", ["page_id"])
    op.create_index(op.f("ix_facebook_accounts_status"), "facebook_accounts", ["status"])

    op.create_table(
        "publish_jobs",
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=True),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_publish_jobs_campaign_id_campaigns"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["copy_drafts.id"],
            name=op.f("fk_publish_jobs_draft_id_copy_drafts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_publish_jobs")),
    )
    op.create_index(op.f("ix_publish_jobs_campaign_id"), "publish_jobs", ["campaign_id"])
    op.create_index(op.f("ix_publish_jobs_channel"), "publish_jobs", ["channel"])
    op.create_index(op.f("ix_publish_jobs_draft_id"), "publish_jobs", ["draft_id"])
    op.create_index(op.f("ix_publish_jobs_external_id"), "publish_jobs", ["external_id"])
    op.create_index(op.f("ix_publish_jobs_status"), "publish_jobs", ["status"])

    op.create_table(
        "insights_daily",
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("publish_job_id", sa.String(length=36), nullable=True),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("spend", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("ctr", sa.Numeric(precision=8, scale=4), nullable=True),
        sa.Column("cpc", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("conversions", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_insights_daily_campaign_id_campaigns"),
        ),
        sa.ForeignKeyConstraint(
            ["publish_job_id"],
            ["publish_jobs.id"],
            name=op.f("fk_insights_daily_publish_job_id_publish_jobs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_insights_daily")),
    )
    op.create_index(op.f("ix_insights_daily_campaign_id"), "insights_daily", ["campaign_id"])
    op.create_index(op.f("ix_insights_daily_metric_date"), "insights_daily", ["metric_date"])
    op.create_index(op.f("ix_insights_daily_publish_job_id"), "insights_daily", ["publish_job_id"])
