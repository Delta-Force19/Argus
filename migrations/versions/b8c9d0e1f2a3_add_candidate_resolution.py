"""add candidate resolution

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_resolution_decisions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "seed_entity_candidate_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column(
            "supersedes_candidate_resolution_decision_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("scope", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewer", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_candidate_resolution_revision_positive",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_candidate_resolution_reason_not_blank",
        ),
        sa.CheckConstraint(
            "length(trim(reviewer)) > 0",
            name="ck_candidate_resolution_reviewer_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name="fk_candidate_resolution_entity_id_entities",
        ),
        sa.ForeignKeyConstraint(
            ["seed_entity_candidate_id"],
            ["entity_candidates.id"],
            name=(
                "fk_candidate_resolution_seed_candidate_id_"
                "entity_candidates"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_candidate_resolution_decision_id"],
            ["candidate_resolution_decisions.id"],
            name=(
                "fk_candidate_resolution_supersedes_id_"
                "candidate_resolution_decisions"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "seed_entity_candidate_id",
            "revision",
            name="uq_candidate_resolution_candidate_revision",
        ),
        sa.UniqueConstraint(
            "supersedes_candidate_resolution_decision_id",
            name="uq_candidate_resolution_supersedes",
        ),
    )
    for column in (
        "seed_entity_candidate_id",
        "supersedes_candidate_resolution_decision_id",
        "status",
        "scope",
        "entity_id",
        "reviewer",
    ):
        op.create_index(
            op.f(f"ix_candidate_resolution_decisions_{column}"),
            "candidate_resolution_decisions",
            [column],
            unique=False,
        )

    with op.batch_alter_table("entities") as batch_op:
        batch_op.alter_column(
            "created_from_alias_decision_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "created_from_candidate_resolution_decision_id",
                sa.Integer(),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            "fk_entities_candidate_creation_decision_id_"
            "candidate_resolution_decisions",
            "candidate_resolution_decisions",
            ["created_from_candidate_resolution_decision_id"],
            ["id"],
        )
        batch_op.create_unique_constraint(
            "uq_entities_candidate_creation_decision",
            ["created_from_candidate_resolution_decision_id"],
        )
        batch_op.create_check_constraint(
            "ck_entities_exactly_one_creation_decision",
            "("
            "created_from_alias_decision_id IS NOT NULL "
            "AND created_from_candidate_resolution_decision_id IS NULL"
            ") OR ("
            "created_from_alias_decision_id IS NULL "
            "AND created_from_candidate_resolution_decision_id IS NOT NULL"
            ")",
        )
        batch_op.create_index(
            op.f(
                "ix_entities_"
                "created_from_candidate_resolution_decision_id"
            ),
            ["created_from_candidate_resolution_decision_id"],
            unique=False,
        )

    with op.batch_alter_table(
        "entity_candidate_assignments"
    ) as batch_op:
        batch_op.alter_column(
            "assigned_by_alias_decision_id",
            existing_type=sa.Integer(),
            nullable=True,
        )
        batch_op.add_column(
            sa.Column(
                "assigned_by_candidate_resolution_decision_id",
                sa.Integer(),
                nullable=True,
            )
        )
        batch_op.create_foreign_key(
            "fk_entity_assignments_candidate_decision_id_"
            "candidate_resolution_decisions",
            "candidate_resolution_decisions",
            ["assigned_by_candidate_resolution_decision_id"],
            ["id"],
        )
        batch_op.create_check_constraint(
            "ck_entity_assignments_exactly_one_decision",
            "("
            "assigned_by_alias_decision_id IS NOT NULL "
            "AND assigned_by_candidate_resolution_decision_id IS NULL"
            ") OR ("
            "assigned_by_alias_decision_id IS NULL "
            "AND assigned_by_candidate_resolution_decision_id IS NOT NULL"
            ")",
        )
        batch_op.create_index(
            op.f(
                "ix_entity_candidate_assignments_"
                "assigned_by_candidate_resolution_decision_id"
            ),
            ["assigned_by_candidate_resolution_decision_id"],
            unique=False,
        )

    op.create_table(
        "candidate_resolution_evidence",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column(
            "candidate_resolution_decision_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_resolution_decision_id"],
            ["candidate_resolution_decisions.id"],
            name=(
                "fk_candidate_resolution_evidence_decision_id_"
                "candidate_resolution_decisions"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["entities.id"],
            name=(
                "fk_candidate_resolution_evidence_entity_id_entities"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "candidate_resolution_decision_id",
            name="uq_candidate_resolution_evidence_decision",
        ),
    )
    for column in (
        "entity_id",
        "candidate_resolution_decision_id",
    ):
        op.create_index(
            op.f(f"ix_candidate_resolution_evidence_{column}"),
            "candidate_resolution_evidence",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in reversed((
        "entity_id",
        "candidate_resolution_decision_id",
    )):
        op.drop_index(
            op.f(f"ix_candidate_resolution_evidence_{column}"),
            table_name="candidate_resolution_evidence",
        )
    op.drop_table("candidate_resolution_evidence")

    with op.batch_alter_table(
        "entity_candidate_assignments"
    ) as batch_op:
        batch_op.drop_index(
            op.f(
                "ix_entity_candidate_assignments_"
                "assigned_by_candidate_resolution_decision_id"
            )
        )
        batch_op.drop_constraint(
            "ck_entity_assignments_exactly_one_decision",
            type_="check",
        )
        batch_op.drop_constraint(
            "fk_entity_assignments_candidate_decision_id_"
            "candidate_resolution_decisions",
            type_="foreignkey",
        )
        batch_op.drop_column(
            "assigned_by_candidate_resolution_decision_id"
        )
        batch_op.alter_column(
            "assigned_by_alias_decision_id",
            existing_type=sa.Integer(),
            nullable=False,
        )

    with op.batch_alter_table("entities") as batch_op:
        batch_op.drop_index(
            op.f(
                "ix_entities_"
                "created_from_candidate_resolution_decision_id"
            )
        )
        batch_op.drop_constraint(
            "ck_entities_exactly_one_creation_decision",
            type_="check",
        )
        batch_op.drop_constraint(
            "uq_entities_candidate_creation_decision",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_entities_candidate_creation_decision_id_"
            "candidate_resolution_decisions",
            type_="foreignkey",
        )
        batch_op.drop_column(
            "created_from_candidate_resolution_decision_id"
        )
        batch_op.alter_column(
            "created_from_alias_decision_id",
            existing_type=sa.Integer(),
            nullable=False,
        )

    for column in reversed((
        "seed_entity_candidate_id",
        "supersedes_candidate_resolution_decision_id",
        "status",
        "scope",
        "entity_id",
        "reviewer",
    )):
        op.drop_index(
            op.f(f"ix_candidate_resolution_decisions_{column}"),
            table_name="candidate_resolution_decisions",
        )
    op.drop_table("candidate_resolution_decisions")
