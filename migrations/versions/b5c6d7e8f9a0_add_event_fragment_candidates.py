"""add source-anchored event fragment candidates

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c6d7e8f9a0"
down_revision: Union[str, Sequence[str], None] = "a4b5c6d7e8f9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_fragment_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column("text_derived_artifact_id", sa.Integer(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        sa.Column("method", sa.String(length=255), nullable=False),
        sa.Column("method_version", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("quality_limitations", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "start_char >= 0",
            name="ck_event_fragment_candidates_start_non_negative",
        ),
        sa.CheckConstraint(
            "end_char > start_char",
            name="ck_event_fragment_candidates_end_after_start",
        ),
        sa.CheckConstraint(
            "length(text_hash) = 64",
            name="ck_event_fragment_candidates_text_hash_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=(
                "fk_event_fragment_candidates_document_version_id_"
                "document_versions"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["text_derived_artifact_id"],
            ["derived_artifacts.id"],
            name=(
                "fk_event_fragment_candidates_text_artifact_id_"
                "derived_artifacts"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "text_derived_artifact_id",
            "start_char",
            "end_char",
            "method",
            "method_version",
            name="uq_event_fragment_candidate_origin",
        ),
    )
    op.create_index(
        "ix_event_fragment_candidates_document_version_id",
        "event_fragment_candidates",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_event_fragment_candidates_text_derived_artifact_id",
        "event_fragment_candidates",
        ["text_derived_artifact_id"],
        unique=False,
    )
    op.create_index(
        "ix_event_fragment_candidates_text_hash",
        "event_fragment_candidates",
        ["text_hash"],
        unique=False,
    )
    op.create_index(
        "ix_event_fragment_candidates_method",
        "event_fragment_candidates",
        ["method"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_event_fragment_candidates_method",
        table_name="event_fragment_candidates",
    )
    op.drop_index(
        "ix_event_fragment_candidates_text_hash",
        table_name="event_fragment_candidates",
    )
    op.drop_index(
        "ix_event_fragment_candidates_text_derived_artifact_id",
        table_name="event_fragment_candidates",
    )
    op.drop_index(
        "ix_event_fragment_candidates_document_version_id",
        table_name="event_fragment_candidates",
    )
    op.drop_table("event_fragment_candidates")
