"""add analysis execution lifecycle and results

Revision ID: e2f3a4b5c6d7
Revises: d0e1f2a3b4c5
Create Date: 2026-07-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f3a4b5c6d7"
down_revision: Union[str, Sequence[str], None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("analysis_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "attempt_count",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column("last_error", sa.Text(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("started_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("finished_at", sa.DateTime(), nullable=True)
        )
        batch_op.create_check_constraint(
            "ck_analysis_runs_attempt_count_non_negative",
            "attempt_count >= 0",
        )

    op.create_table(
        "analysis_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column(
            "result_schema_version",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
        sa.Column("output_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(output_hash) = 64",
            name="ck_analysis_results_output_hash_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_runs.id"],
            name="fk_analysis_results_analysis_run_id_analysis_runs",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_run_id",
            name="uq_analysis_results_analysis_run_id",
        ),
    )
    op.create_index(
        op.f("ix_analysis_results_analysis_run_id"),
        "analysis_results",
        ["analysis_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_analysis_results_output_hash"),
        "analysis_results",
        ["output_hash"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_analysis_results_output_hash"),
        table_name="analysis_results",
    )
    op.drop_index(
        op.f("ix_analysis_results_analysis_run_id"),
        table_name="analysis_results",
    )
    op.drop_table("analysis_results")
    with op.batch_alter_table("analysis_runs") as batch_op:
        batch_op.drop_constraint(
            "ck_analysis_runs_attempt_count_non_negative",
            type_="check",
        )
        batch_op.drop_column("finished_at")
        batch_op.drop_column("started_at")
        batch_op.drop_column("last_error")
        batch_op.drop_column("attempt_count")
