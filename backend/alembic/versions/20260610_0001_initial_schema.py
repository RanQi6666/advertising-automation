"""initial schema

Revision ID: 20260610_0001
Revises:
Create Date: 2026-06-10 00:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260610_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "clients",
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("industry", sa.String(length=128), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clients")),
    )
    op.create_index(op.f("ix_clients_name"), "clients", ["name"], unique=True)

    op.create_table(
        "prompt_versions",
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompt_versions")),
    )
    op.create_index(op.f("ix_prompt_versions_is_active"), "prompt_versions", ["is_active"])
    op.create_index(op.f("ix_prompt_versions_name"), "prompt_versions", ["name"])
    op.create_index(op.f("ix_prompt_versions_version"), "prompt_versions", ["version"])

    op.create_table(
        "brands",
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("voice", sa.Text(), nullable=True),
        sa.Column("guidelines", sa.Text(), nullable=True),
        sa.Column("banned_words", sa.JSON(), nullable=False),
        sa.Column("compliance_notes", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name=op.f("fk_brands_client_id_clients")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brands")),
    )
    op.create_index(op.f("ix_brands_client_id"), "brands", ["client_id"])
    op.create_index(op.f("ix_brands_name"), "brands", ["name"])

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
        "campaigns",
        sa.Column("client_id", sa.String(length=36), nullable=False),
        sa.Column("brand_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("objective", sa.String(length=128), nullable=True),
        sa.Column("product_name", sa.String(length=255), nullable=True),
        sa.Column("audience_description", sa.Text(), nullable=True),
        sa.Column("budget_notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["brand_id"], ["brands.id"], name=op.f("fk_campaigns_brand_id_brands")
        ),
        sa.ForeignKeyConstraint(
            ["client_id"], ["clients.id"], name=op.f("fk_campaigns_client_id_clients")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
    )
    op.create_index(op.f("ix_campaigns_brand_id"), "campaigns", ["brand_id"])
    op.create_index(op.f("ix_campaigns_client_id"), "campaigns", ["client_id"])
    op.create_index(op.f("ix_campaigns_name"), "campaigns", ["name"])
    op.create_index(op.f("ix_campaigns_status"), "campaigns", ["status"])

    op.create_table(
        "agent_runs",
        sa.Column("campaign_id", sa.String(length=36), nullable=True),
        sa.Column("graph_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_state", sa.JSON(), nullable=False),
        sa.Column("output_state", sa.JSON(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_agent_runs_campaign_id_campaigns"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_runs")),
    )
    op.create_index(op.f("ix_agent_runs_campaign_id"), "agent_runs", ["campaign_id"])
    op.create_index(op.f("ix_agent_runs_graph_name"), "agent_runs", ["graph_name"])
    op.create_index(op.f("ix_agent_runs_status"), "agent_runs", ["status"])

    op.create_table(
        "content_topics",
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("angle", sa.Text(), nullable=False),
        sa.Column("audience", sa.Text(), nullable=True),
        sa.Column("selling_points", sa.JSON(), nullable=False),
        sa.Column("risk_notes", sa.Text(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("source_data", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_content_topics_campaign_id_campaigns"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_content_topics")),
    )
    op.create_index(op.f("ix_content_topics_campaign_id"), "content_topics", ["campaign_id"])
    op.create_index(op.f("ix_content_topics_status"), "content_topics", ["status"])

    op.create_table(
        "review_tasks",
        sa.Column("campaign_id", sa.String(length=36), nullable=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("reviewer_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("decision", sa.String(length=32), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_review_tasks_campaign_id_campaigns"),
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name=op.f("fk_review_tasks_reviewer_id_users"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_review_tasks")),
    )
    op.create_index(op.f("ix_review_tasks_campaign_id"), "review_tasks", ["campaign_id"])
    op.create_index(op.f("ix_review_tasks_entity_id"), "review_tasks", ["entity_id"])
    op.create_index(op.f("ix_review_tasks_entity_type"), "review_tasks", ["entity_type"])
    op.create_index(op.f("ix_review_tasks_reviewer_id"), "review_tasks", ["reviewer_id"])
    op.create_index(op.f("ix_review_tasks_status"), "review_tasks", ["status"])

    op.create_table(
        "copy_drafts",
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("topic_id", sa.String(length=36), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("primary_text", sa.Text(), nullable=True),
        sa.Column("headline", sa.String(length=255), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cta", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_copy_drafts_campaign_id_campaigns"),
        ),
        sa.ForeignKeyConstraint(
            ["topic_id"],
            ["content_topics.id"],
            name=op.f("fk_copy_drafts_topic_id_content_topics"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_copy_drafts")),
    )
    op.create_index(op.f("ix_copy_drafts_campaign_id"), "copy_drafts", ["campaign_id"])
    op.create_index(op.f("ix_copy_drafts_status"), "copy_drafts", ["status"])
    op.create_index(op.f("ix_copy_drafts_topic_id"), "copy_drafts", ["topic_id"])

    op.create_table(
        "creative_assets",
        sa.Column("campaign_id", sa.String(length=36), nullable=False),
        sa.Column("draft_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("alt_text", sa.Text(), nullable=True),
        sa.Column("size", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_creative_assets_campaign_id_campaigns"),
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["copy_drafts.id"],
            name=op.f("fk_creative_assets_draft_id_copy_drafts"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_creative_assets")),
    )
    op.create_index(op.f("ix_creative_assets_campaign_id"), "creative_assets", ["campaign_id"])
    op.create_index(op.f("ix_creative_assets_draft_id"), "creative_assets", ["draft_id"])
    op.create_index(op.f("ix_creative_assets_status"), "creative_assets", ["status"])

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


def downgrade() -> None:
    op.drop_index(op.f("ix_insights_daily_publish_job_id"), table_name="insights_daily")
    op.drop_index(op.f("ix_insights_daily_metric_date"), table_name="insights_daily")
    op.drop_index(op.f("ix_insights_daily_campaign_id"), table_name="insights_daily")
    op.drop_table("insights_daily")
    op.drop_index(op.f("ix_publish_jobs_status"), table_name="publish_jobs")
    op.drop_index(op.f("ix_publish_jobs_external_id"), table_name="publish_jobs")
    op.drop_index(op.f("ix_publish_jobs_draft_id"), table_name="publish_jobs")
    op.drop_index(op.f("ix_publish_jobs_channel"), table_name="publish_jobs")
    op.drop_index(op.f("ix_publish_jobs_campaign_id"), table_name="publish_jobs")
    op.drop_table("publish_jobs")
    op.drop_index(op.f("ix_creative_assets_status"), table_name="creative_assets")
    op.drop_index(op.f("ix_creative_assets_draft_id"), table_name="creative_assets")
    op.drop_index(op.f("ix_creative_assets_campaign_id"), table_name="creative_assets")
    op.drop_table("creative_assets")
    op.drop_index(op.f("ix_copy_drafts_topic_id"), table_name="copy_drafts")
    op.drop_index(op.f("ix_copy_drafts_status"), table_name="copy_drafts")
    op.drop_index(op.f("ix_copy_drafts_campaign_id"), table_name="copy_drafts")
    op.drop_table("copy_drafts")
    op.drop_index(op.f("ix_review_tasks_status"), table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_reviewer_id"), table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_entity_type"), table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_entity_id"), table_name="review_tasks")
    op.drop_index(op.f("ix_review_tasks_campaign_id"), table_name="review_tasks")
    op.drop_table("review_tasks")
    op.drop_index(op.f("ix_content_topics_status"), table_name="content_topics")
    op.drop_index(op.f("ix_content_topics_campaign_id"), table_name="content_topics")
    op.drop_table("content_topics")
    op.drop_index(op.f("ix_agent_runs_status"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_graph_name"), table_name="agent_runs")
    op.drop_index(op.f("ix_agent_runs_campaign_id"), table_name="agent_runs")
    op.drop_table("agent_runs")
    op.drop_index(op.f("ix_campaigns_status"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_name"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_client_id"), table_name="campaigns")
    op.drop_index(op.f("ix_campaigns_brand_id"), table_name="campaigns")
    op.drop_table("campaigns")
    op.drop_index(op.f("ix_facebook_accounts_status"), table_name="facebook_accounts")
    op.drop_index(op.f("ix_facebook_accounts_page_id"), table_name="facebook_accounts")
    op.drop_index(op.f("ix_facebook_accounts_client_id"), table_name="facebook_accounts")
    op.drop_index(op.f("ix_facebook_accounts_ad_account_id"), table_name="facebook_accounts")
    op.drop_table("facebook_accounts")
    op.drop_index(op.f("ix_brands_name"), table_name="brands")
    op.drop_index(op.f("ix_brands_client_id"), table_name="brands")
    op.drop_table("brands")
    op.drop_index(op.f("ix_prompt_versions_version"), table_name="prompt_versions")
    op.drop_index(op.f("ix_prompt_versions_name"), table_name="prompt_versions")
    op.drop_index(op.f("ix_prompt_versions_is_active"), table_name="prompt_versions")
    op.drop_table("prompt_versions")
    op.drop_index(op.f("ix_clients_name"), table_name="clients")
    op.drop_table("clients")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")
