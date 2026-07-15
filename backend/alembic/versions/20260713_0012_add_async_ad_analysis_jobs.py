"""add async ad analysis jobs

Revision ID: 20260713_0012
Revises: 20260702_0011
Create Date: 2026-07-13 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260713_0012"
down_revision: str | None = "20260702_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ad_performance_analyses", sa.Column("analysis_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("external_request_id", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "ad_performance_analyses", sa.Column("payload_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "ad_performance_analyses", sa.Column("normalized_payload", sa.JSON(), nullable=True)
    )
    op.add_column(
        "ad_performance_analyses", sa.Column("stage", sa.String(length=64), nullable=True)
    )
    op.add_column("ad_performance_analyses", sa.Column("progress", sa.Integer(), nullable=True))
    op.add_column(
        "ad_performance_analyses", sa.Column("analysis_scope", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("result_schema_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("generation_task_id", sa.String(length=36), nullable=True),
    )
    op.add_column("ad_performance_analyses", sa.Column("media_summary", sa.JSON(), nullable=True))
    op.add_column(
        "ad_performance_analyses", sa.Column("research_summary", sa.JSON(), nullable=True)
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("last_dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("dispatch_claimed_by", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("dispatch_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("dispatch_attempts", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column("ad_performance_analyses", sa.Column("dispatch_error", sa.Text(), nullable=True))
    op.add_column(
        "ad_performance_analyses",
        sa.Column("attempt_count", sa.Integer(), nullable=True, server_default="0"),
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("max_attempts", sa.Integer(), nullable=True, server_default="2"),
    )
    op.add_column(
        "ad_performance_analyses", sa.Column("error_code", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "ad_performance_analyses",
        sa.Column("error_retryable", sa.Boolean(), nullable=True, server_default=sa.false()),
    )

    op.create_unique_constraint(
        op.f("uq_ad_performance_analyses_analysis_id"), "ad_performance_analyses", ["analysis_id"]
    )
    op.create_unique_constraint(
        op.f("uq_ad_performance_analyses_external_request_id"),
        "ad_performance_analyses",
        ["external_request_id"],
    )
    op.create_unique_constraint(
        op.f("uq_ad_performance_analyses_generation_task_id"),
        "ad_performance_analyses",
        ["generation_task_id"],
    )
    op.create_foreign_key(
        op.f("fk_ad_performance_analyses_generation_task_id_generation_tasks"),
        "ad_performance_analyses",
        "generation_tasks",
        ["generation_task_id"],
        ["id"],
        ondelete="SET NULL",
    )

    for column in (
        "analysis_id",
        "external_request_id",
        "payload_hash",
        "stage",
        "analysis_scope",
        "result_schema_version",
        "generation_task_id",
        "dispatch_claimed_by",
        "error_code",
    ):
        op.create_index(
            op.f(f"ix_ad_performance_analyses_{column}"), "ad_performance_analyses", [column]
        )

    op.create_table(
        "ad_analysis_reference_ads",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("analysis_record_id", sa.String(length=36), nullable=False),
        sa.Column("reference_id", sa.String(length=128), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("source_domain", sa.String(length=255), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("similarity_score", sa.Float(), nullable=True),
        sa.Column("performance_evidence_json", sa.JSON(), nullable=True),
        sa.Column("creative_analysis_json", sa.JSON(), nullable=True),
        sa.Column("raw_excerpt_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["analysis_record_id"],
            ["ad_performance_analyses.id"],
            name=op.f("fk_ad_analysis_reference_ads_analysis_record_id_ad_performance_analyses"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ad_analysis_reference_ads")),
    )
    for column in (
        "analysis_record_id",
        "reference_id",
        "source_type",
        "source_domain",
        "content_hash",
    ):
        op.create_index(
            op.f(f"ix_ad_analysis_reference_ads_{column}"), "ad_analysis_reference_ads", [column]
        )


def downgrade() -> None:
    for column in (
        "analysis_record_id",
        "reference_id",
        "source_type",
        "source_domain",
        "content_hash",
    ):
        op.drop_index(
            op.f(f"ix_ad_analysis_reference_ads_{column}"), table_name="ad_analysis_reference_ads"
        )
    op.drop_table("ad_analysis_reference_ads")

    for column in (
        "error_code",
        "dispatch_claimed_by",
        "generation_task_id",
        "result_schema_version",
        "analysis_scope",
        "stage",
        "payload_hash",
        "external_request_id",
        "analysis_id",
    ):
        op.drop_index(
            op.f(f"ix_ad_performance_analyses_{column}"), table_name="ad_performance_analyses"
        )
    op.drop_constraint(
        op.f("fk_ad_performance_analyses_generation_task_id_generation_tasks"),
        "ad_performance_analyses",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("uq_ad_performance_analyses_generation_task_id"),
        "ad_performance_analyses",
        type_="unique",
    )
    op.drop_constraint(
        op.f("uq_ad_performance_analyses_external_request_id"),
        "ad_performance_analyses",
        type_="unique",
    )
    op.drop_constraint(
        op.f("uq_ad_performance_analyses_analysis_id"), "ad_performance_analyses", type_="unique"
    )

    for column in (
        "error_retryable",
        "error_code",
        "max_attempts",
        "attempt_count",
        "dispatch_error",
        "dispatch_attempts",
        "dispatch_claimed_at",
        "dispatch_claimed_by",
        "last_dispatched_at",
        "completed_at",
        "started_at",
        "research_summary",
        "media_summary",
        "generation_task_id",
        "result_schema_version",
        "analysis_scope",
        "progress",
        "stage",
        "normalized_payload",
        "payload_hash",
        "external_request_id",
        "analysis_id",
    ):
        op.drop_column("ad_performance_analyses", column)
