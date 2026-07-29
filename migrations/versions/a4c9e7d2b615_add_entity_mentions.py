"""add entity mentions

Revision ID: a4c9e7d2b615
Revises: f6a8d3c91b42
Create Date: 2026-07-27

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4c9e7d2b615"
down_revision: Union[str, Sequence[str], None] = "f6a8d3c91b42"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "entity_mentions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("derived_artifact_id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("source_label", sa.String(length=100), nullable=False),
        sa.Column("surface_text", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "end_char > start_char",
            name="ck_entity_mentions_end_after_start",
        ),
        sa.CheckConstraint(
            "start_char >= 0",
            name="ck_entity_mentions_start_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["derived_artifact_id"],
            ["derived_artifacts.id"],
            name=(
                "fk_entity_mentions_derived_artifact_id_"
                "derived_artifacts"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=(
                "fk_entity_mentions_document_version_id_"
                "document_versions"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "derived_artifact_id",
            "start_char",
            "end_char",
            "source_label",
            name="uq_entity_mention_artifact_span_label",
        ),
    )
    op.create_index(
        op.f("ix_entity_mentions_derived_artifact_id"),
        "entity_mentions",
        ["derived_artifact_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_mentions_document_version_id"),
        "entity_mentions",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_mentions_entity_type"),
        "entity_mentions",
        ["entity_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_mentions_normalized_text"),
        "entity_mentions",
        ["normalized_text"],
        unique=False,
    )
    op.create_index(
        op.f("ix_entity_mentions_source_label"),
        "entity_mentions",
        ["source_label"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_entity_mentions_source_label"),
        table_name="entity_mentions",
    )
    op.drop_index(
        op.f("ix_entity_mentions_normalized_text"),
        table_name="entity_mentions",
    )
    op.drop_index(
        op.f("ix_entity_mentions_entity_type"),
        table_name="entity_mentions",
    )
    op.drop_index(
        op.f("ix_entity_mentions_document_version_id"),
        table_name="entity_mentions",
    )
    op.drop_index(
        op.f("ix_entity_mentions_derived_artifact_id"),
        table_name="entity_mentions",
    )
    op.drop_table("entity_mentions")
