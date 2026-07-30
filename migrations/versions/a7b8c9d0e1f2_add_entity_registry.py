"""add entity registry

Revision ID: a7b8c9d0e1f2
Revises: f2a3b4c5d6e7
Create Date: 2026-07-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("canonical_name", sa.Text(), nullable=False),
        sa.Column(
            "canonical_entity_candidate_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "created_from_alias_decision_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(trim(canonical_name)) > 0",
            name="ck_entities_canonical_name_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["canonical_entity_candidate_id"],
            ["entity_candidates.id"],
            name=(
                "fk_entities_canonical_candidate_id_"
                "entity_candidates"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["created_from_alias_decision_id"],
            ["alias_decisions.id"],
            name=(
                "fk_entities_creation_decision_id_"
                "alias_decisions"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "canonical_entity_candidate_id",
            name="uq_entities_canonical_candidate",
        ),
        sa.UniqueConstraint(
            "created_from_alias_decision_id",
            name="uq_entities_creation_decision",
        ),
    )
    for column in (
        "entity_type",
        "canonical_name",
        "canonical_entity_candidate_id",
        "created_from_alias_decision_id",
    ):
        op.create_index(
            op.f(f"ix_entities_{column}"),
            "entities",
            [column],
            unique=False,
        )

    op.create_table(
        "entity_candidate_assignments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("entity_candidate_id", sa.Integer(), nullable=False),
        sa.Column(
            "assigned_by_alias_decision_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=(
                "fk_entity_candidate_assignments_entity_id_entities"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["entity_candidate_id"],
            ["entity_candidates.id"],
            name=(
                "fk_entity_candidate_assignments_candidate_id_"
                "entity_candidates"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["assigned_by_alias_decision_id"],
            ["alias_decisions.id"],
            name=(
                "fk_entity_candidate_assignments_decision_id_"
                "alias_decisions"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_candidate_id",
            name="uq_entity_candidate_assignments_candidate",
        ),
    )
    for column in (
        "entity_id",
        "entity_candidate_id",
        "assigned_by_alias_decision_id",
    ):
        op.create_index(
            op.f(f"ix_entity_candidate_assignments_{column}"),
            "entity_candidate_assignments",
            [column],
            unique=False,
        )

    op.create_table(
        "entity_resolution_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("alias_decision_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=(
                "fk_entity_resolution_evidence_entity_id_entities"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["alias_decision_id"],
            ["alias_decisions.id"],
            name=(
                "fk_entity_resolution_evidence_decision_id_"
                "alias_decisions"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "alias_decision_id",
            name="uq_entity_resolution_evidence_decision",
        ),
    )
    for column in ("entity_id", "alias_decision_id"):
        op.create_index(
            op.f(f"ix_entity_resolution_evidence_{column}"),
            "entity_resolution_evidence",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in reversed(("entity_id", "alias_decision_id")):
        op.drop_index(
            op.f(f"ix_entity_resolution_evidence_{column}"),
            table_name="entity_resolution_evidence",
        )
    op.drop_table("entity_resolution_evidence")

    for column in reversed((
        "entity_id",
        "entity_candidate_id",
        "assigned_by_alias_decision_id",
    )):
        op.drop_index(
            op.f(f"ix_entity_candidate_assignments_{column}"),
            table_name="entity_candidate_assignments",
        )
    op.drop_table("entity_candidate_assignments")

    for column in reversed((
        "entity_type",
        "canonical_name",
        "canonical_entity_candidate_id",
        "created_from_alias_decision_id",
    )):
        op.drop_index(
            op.f(f"ix_entities_{column}"),
            table_name="entities",
        )
    op.drop_table("entities")
