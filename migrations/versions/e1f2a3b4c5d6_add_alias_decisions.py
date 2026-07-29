"""add alias decisions

Revision ID: e1f2a3b4c5d6
Revises: d9e1f2a3b4c5
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d9e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alias_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("alias_proposal_id", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "supersedes_alias_decision_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_alias_decisions_revision_positive",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_alias_decisions_reason_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(reviewer)) > 0",
            name="ck_alias_decisions_reviewer_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["alias_proposal_id"],
            ["alias_proposals.id"],
            name=(
                "fk_alias_decisions_alias_proposal_id_"
                "alias_proposals"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_alias_decision_id"],
            ["alias_decisions.id"],
            name=(
                "fk_alias_decisions_supersedes_id_"
                "alias_decisions"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "alias_proposal_id",
            "revision",
            name="uq_alias_decision_proposal_revision",
        ),
        sa.UniqueConstraint(
            "supersedes_alias_decision_id",
            name="uq_alias_decision_supersedes",
        ),
    )
    for column in (
        "alias_proposal_id",
        "supersedes_alias_decision_id",
        "status",
        "reviewer",
    ):
        op.create_index(
            op.f(f"ix_alias_decisions_{column}"),
            "alias_decisions",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in reversed((
        "alias_proposal_id",
        "supersedes_alias_decision_id",
        "status",
        "reviewer",
    )):
        op.drop_index(
            op.f(f"ix_alias_decisions_{column}"),
            table_name="alias_decisions",
        )
    op.drop_table("alias_decisions")
