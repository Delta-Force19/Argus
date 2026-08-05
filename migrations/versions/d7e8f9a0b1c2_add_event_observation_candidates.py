"""add source-located event observation candidates

Revision ID: d7e8f9a0b1c2
Revises: c6d7e8f9a0b1
Create Date: 2026-08-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d7e8f9a0b1c2"
down_revision: Union[str, Sequence[str], None] = "c6d7e8f9a0b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event_observation_candidates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("derived_artifact_id", sa.Integer(), nullable=False),
        sa.Column(
            "event_fragment_candidate_id", sa.Integer(), nullable=False
        ),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column(
            "observation_type",
            sa.Enum(
                "participant_mention",
                "place_mention",
                "time_mention",
                "event_mention",
                "action_candidate",
                "object_candidate",
                name="event_observation_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("source_label", sa.String(length=100), nullable=False),
        sa.Column("surface_text", sa.Text(), nullable=False),
        sa.Column("normalized_value", sa.Text(), nullable=False),
        sa.Column("start_char", sa.Integer(), nullable=False),
        sa.Column("end_char", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "start_char >= 0",
            name="ck_event_observation_candidates_start_non_negative",
        ),
        sa.CheckConstraint(
            "end_char > start_char",
            name="ck_event_observation_candidates_end_after_start",
        ),
        sa.ForeignKeyConstraint(
            ["derived_artifact_id"],
            ["derived_artifacts.id"],
            name=(
                "fk_event_observation_candidates_derived_artifact_id_"
                "derived_artifacts"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["event_fragment_candidate_id"],
            ["event_fragment_candidates.id"],
            name=(
                "fk_event_observation_candidates_fragment_id_"
                "event_fragment_candidates"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=(
                "fk_event_observation_candidates_document_version_id_"
                "document_versions"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "derived_artifact_id",
            "event_fragment_candidate_id",
            "observation_type",
            "start_char",
            "end_char",
            "source_label",
            name="uq_event_observation_candidate_origin",
        ),
    )
    for column in (
        "derived_artifact_id",
        "event_fragment_candidate_id",
        "document_version_id",
        "observation_type",
        "source_label",
        "normalized_value",
    ):
        op.create_index(
            f"ix_event_observation_candidates_{column}",
            "event_observation_candidates",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in reversed((
        "derived_artifact_id",
        "event_fragment_candidate_id",
        "document_version_id",
        "observation_type",
        "source_label",
        "normalized_value",
    )):
        op.drop_index(
            f"ix_event_observation_candidates_{column}",
            table_name="event_observation_candidates",
        )
    op.drop_table("event_observation_candidates")
