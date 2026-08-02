"""add source-located evidence for reproducible analysis results

Revision ID: a4b5c6d7e8f9
Revises: f3a4b5c6d7e8
Create Date: 2026-08-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b5c6d7e8f9"
down_revision: Union[str, Sequence[str], None] = "f3a4b5c6d7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Preserve the legacy discourse pipeline and its existing evidence while
    # freeing the canonical name for AnalysisRun-owned evidence.
    op.rename_table("analysis_evidence", "discourse_analysis_evidence")
    op.drop_index(
        "ix_analysis_evidence_analysis_result_id",
        table_name="discourse_analysis_evidence",
    )
    op.drop_index(
        "ix_analysis_evidence_category",
        table_name="discourse_analysis_evidence",
    )
    op.create_index(
        "ix_discourse_analysis_evidence_analysis_result_id",
        "discourse_analysis_evidence",
        ["analysis_result_id"],
        unique=False,
    )
    op.create_index(
        "ix_discourse_analysis_evidence_category",
        "discourse_analysis_evidence",
        ["category"],
        unique=False,
    )

    with op.batch_alter_table("analysis_results") as batch_op:
        batch_op.add_column(
            sa.Column("evidence_set_hash", sa.String(64), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_analysis_results_evidence_set_hash_sha256",
            (
                "evidence_set_hash IS NULL "
                "OR length(evidence_set_hash) = 64"
            ),
        )
        batch_op.create_index(
            "ix_analysis_results_evidence_set_hash",
            ["evidence_set_hash"],
            unique=False,
        )

    op.create_table(
        "analysis_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_result_id", sa.Integer(), nullable=False),
        sa.Column("evidence_index", sa.Integer(), nullable=False),
        sa.Column(
            "evidence_schema_version",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column("modality", sa.String(length=20), nullable=False),
        sa.Column("locator", sa.JSON(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "evidence_index >= 0",
            name="ck_analysis_evidence_index_non_negative",
        ),
        sa.CheckConstraint(
            "length(evidence_hash) = 64",
            name="ck_analysis_evidence_hash_sha256",
        ),
        sa.CheckConstraint(
            "modality IN ('text', 'image', 'audio', 'video')",
            name="ck_analysis_evidence_modality",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_result_id"],
            ["analysis_results.id"],
            name=(
                "fk_analysis_evidence_analysis_result_id_"
                "analysis_results"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_result_id",
            "evidence_index",
            name="uq_analysis_evidence_result_index",
        ),
    )
    op.create_index(
        "ix_analysis_evidence_analysis_result_id",
        "analysis_evidence",
        ["analysis_result_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_evidence_category",
        "analysis_evidence",
        ["category"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_evidence_modality",
        "analysis_evidence",
        ["modality"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_evidence_evidence_hash",
        "analysis_evidence",
        ["evidence_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_analysis_evidence_evidence_hash",
        table_name="analysis_evidence",
    )
    op.drop_index(
        "ix_analysis_evidence_modality",
        table_name="analysis_evidence",
    )
    op.drop_index(
        "ix_analysis_evidence_category",
        table_name="analysis_evidence",
    )
    op.drop_index(
        "ix_analysis_evidence_analysis_result_id",
        table_name="analysis_evidence",
    )
    op.drop_table("analysis_evidence")

    with op.batch_alter_table("analysis_results") as batch_op:
        batch_op.drop_index("ix_analysis_results_evidence_set_hash")
        batch_op.drop_constraint(
            "ck_analysis_results_evidence_set_hash_sha256",
            type_="check",
        )
        batch_op.drop_column("evidence_set_hash")

    op.drop_index(
        "ix_discourse_analysis_evidence_category",
        table_name="discourse_analysis_evidence",
    )
    op.drop_index(
        "ix_discourse_analysis_evidence_analysis_result_id",
        table_name="discourse_analysis_evidence",
    )
    op.rename_table("discourse_analysis_evidence", "analysis_evidence")
    op.create_index(
        "ix_analysis_evidence_analysis_result_id",
        "analysis_evidence",
        ["analysis_result_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_evidence_category",
        "analysis_evidence",
        ["category"],
        unique=False,
    )
