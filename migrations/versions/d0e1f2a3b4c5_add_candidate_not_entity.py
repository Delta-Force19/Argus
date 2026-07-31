"""add candidate not-entity evidence

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-31

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_resolution_exclusions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "candidate_resolution_decision_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "entity_candidate_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_resolution_decision_id"],
            ["candidate_resolution_decisions.id"],
            name=(
                "fk_candidate_resolution_exclusion_decision_id_"
                "candidate_resolution_decisions"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["entity_candidate_id"],
            ["entity_candidates.id"],
            name=(
                "fk_candidate_resolution_exclusion_candidate_id_"
                "entity_candidates"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_resolution_decision_id",
            "entity_candidate_id",
            name=(
                "uq_candidate_resolution_exclusion_decision_candidate"
            ),
        ),
    )
    for column in (
        "candidate_resolution_decision_id",
        "entity_candidate_id",
    ):
        op.create_index(
            op.f(f"ix_candidate_resolution_exclusions_{column}"),
            "candidate_resolution_exclusions",
            [column],
            unique=False,
        )


def downgrade() -> None:
    op.drop_table("candidate_resolution_exclusions")
