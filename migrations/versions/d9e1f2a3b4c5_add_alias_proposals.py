"""add alias proposals

Revision ID: d9e1f2a3b4c5
Revises: c7d8e9f0a1b2
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d9e1f2a3b4c5"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alias_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("derived_artifact_id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column(
            "left_entity_candidate_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "right_entity_candidate_id",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("left_canonical_text", sa.Text(), nullable=False),
        sa.Column("right_canonical_text", sa.Text(), nullable=False),
        sa.Column("signal_type", sa.String(length=50), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column(
            "confidence_basis",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "left_occurrence_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "right_occurrence_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column(
            "shared_document_count",
            sa.Integer(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "right_entity_candidate_id > left_entity_candidate_id",
            name="ck_alias_proposals_candidate_order",
        ),
        sa.CheckConstraint(
            "confidence_score >= 0.0 AND confidence_score <= 1.0",
            name="ck_alias_proposals_confidence_range",
        ),
        sa.CheckConstraint(
            "left_occurrence_count >= 1",
            name="ck_alias_proposals_left_count_positive",
        ),
        sa.CheckConstraint(
            "right_occurrence_count >= 1",
            name="ck_alias_proposals_right_count_positive",
        ),
        sa.CheckConstraint(
            "shared_document_count >= 1",
            name="ck_alias_proposals_shared_documents_positive",
        ),
        sa.ForeignKeyConstraint(
            ["derived_artifact_id"],
            ["derived_artifacts.id"],
            name=(
                "fk_alias_proposals_derived_artifact_id_"
                "derived_artifacts"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=(
                "fk_alias_proposals_document_version_id_"
                "document_versions"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["left_entity_candidate_id"],
            ["entity_candidates.id"],
            name=(
                "fk_alias_proposals_left_candidate_id_"
                "entity_candidates"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["right_entity_candidate_id"],
            ["entity_candidates.id"],
            name=(
                "fk_alias_proposals_right_candidate_id_"
                "entity_candidates"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "derived_artifact_id",
            "left_entity_candidate_id",
            "right_entity_candidate_id",
            "signal_type",
            name="uq_alias_proposal_artifact_pair_signal",
        ),
    )
    for column in (
        "derived_artifact_id",
        "document_version_id",
        "left_entity_candidate_id",
        "right_entity_candidate_id",
        "entity_type",
        "left_canonical_text",
        "right_canonical_text",
        "signal_type",
    ):
        op.create_index(
            op.f(f"ix_alias_proposals_{column}"),
            "alias_proposals",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in reversed((
        "derived_artifact_id",
        "document_version_id",
        "left_entity_candidate_id",
        "right_entity_candidate_id",
        "entity_type",
        "left_canonical_text",
        "right_canonical_text",
        "signal_type",
    )):
        op.drop_index(
            op.f(f"ix_alias_proposals_{column}"),
            table_name="alias_proposals",
        )
    op.drop_table("alias_proposals")
