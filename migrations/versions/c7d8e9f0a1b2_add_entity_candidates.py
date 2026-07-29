"""add entity candidates

Revision ID: c7d8e9f0a1b2
Revises: a4c9e7d2b615
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d8e9f0a1b2"
down_revision: Union[str, Sequence[str], None] = "a4c9e7d2b615"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("derived_artifact_id", sa.Integer(), nullable=False),
        sa.Column("entity_mention_id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("canonical_text", sa.Text(), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=False),
        sa.Column("context_start_char", sa.Integer(), nullable=False),
        sa.Column("context_end_char", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "context_end_char > context_start_char",
            name="ck_entity_candidates_context_end_after_start",
        ),
        sa.CheckConstraint(
            "context_start_char >= 0",
            name="ck_entity_candidates_context_start_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["derived_artifact_id"],
            ["derived_artifacts.id"],
            name=(
                "fk_entity_candidates_derived_artifact_id_"
                "derived_artifacts"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=(
                "fk_entity_candidates_document_version_id_"
                "document_versions"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["entity_mention_id"],
            ["entity_mentions.id"],
            name=(
                "fk_entity_candidates_entity_mention_id_entity_mentions"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "derived_artifact_id",
            "entity_mention_id",
            name="uq_entity_candidate_artifact_mention",
        ),
    )
    op.create_index(
        op.f("ix_entity_candidates_canonical_text"),
        "entity_candidates",
        ["canonical_text"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_candidates_derived_artifact_id"),
        "entity_candidates",
        ["derived_artifact_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_candidates_document_version_id"),
        "entity_candidates",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_candidates_entity_mention_id"),
        "entity_candidates",
        ["entity_mention_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_candidates_entity_type"),
        "entity_candidates",
        ["entity_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_entity_candidates_entity_type"),
        table_name="entity_candidates",
    )
    op.drop_index(
        op.f("ix_entity_candidates_entity_mention_id"),
        table_name="entity_candidates",
    )
    op.drop_index(
        op.f("ix_entity_candidates_document_version_id"),
        table_name="entity_candidates",
    )
    op.drop_index(
        op.f("ix_entity_candidates_derived_artifact_id"),
        table_name="entity_candidates",
    )
    op.drop_index(
        op.f("ix_entity_candidates_canonical_text"),
        table_name="entity_candidates",
    )
    op.drop_table("entity_candidates")
