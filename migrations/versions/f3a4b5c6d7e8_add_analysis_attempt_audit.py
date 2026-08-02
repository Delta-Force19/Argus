"""add immutable analysis execution attempt audit

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a4b5c6d7e8"
down_revision: Union[str, Sequence[str], None] = "e2f3a4b5c6d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_execution_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("analysis_run_id", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("recovery_operator", sa.String(length=255), nullable=True),
        sa.Column("recovery_reason", sa.Text(), nullable=True),
        sa.Column(
            "migrated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_analysis_execution_attempts_number_positive",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed', 'abandoned')",
            name="ck_analysis_execution_attempts_status",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_runs.id"],
            name="fk_analysis_execution_attempts_run_id_analysis_runs",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "analysis_run_id",
            "attempt_number",
            name="uq_analysis_execution_attempts_run_number",
        ),
    )
    op.create_index(
        op.f("ix_analysis_execution_attempts_analysis_run_id"),
        "analysis_execution_attempts",
        ["analysis_run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_analysis_execution_attempts_status"),
        "analysis_execution_attempts",
        ["status"],
        unique=False,
    )

    # Earlier schema retained only the latest lifecycle fields. Preserve that
    # reconstructable attempt explicitly and mark it as migrated; missing
    # earlier retry detail is never invented.
    op.execute(sa.text("""
        INSERT INTO analysis_execution_attempts (
            analysis_run_id,
            attempt_number,
            status,
            started_at,
            finished_at,
            error,
            migrated
        )
        SELECT
            id,
            attempt_count,
            CASE status
                WHEN 'running' THEN 'running'
                WHEN 'completed' THEN 'completed'
                ELSE 'failed'
            END,
            COALESCE(started_at, created_at),
            finished_at,
            last_error,
            1
        FROM analysis_runs
        WHERE attempt_count > 0
    """))


def downgrade() -> None:
    op.drop_index(
        op.f("ix_analysis_execution_attempts_status"),
        table_name="analysis_execution_attempts",
    )
    op.drop_index(
        op.f("ix_analysis_execution_attempts_analysis_run_id"),
        table_name="analysis_execution_attempts",
    )
    op.drop_table("analysis_execution_attempts")
