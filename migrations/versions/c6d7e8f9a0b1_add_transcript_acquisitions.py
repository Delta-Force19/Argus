"""add transcript acquisitions

Revision ID: c6d7e8f9a0b1
Revises: b5c6d7e8f9a0
Create Date: 2026-08-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c6d7e8f9a0b1"
down_revision: Union[str, Sequence[str], None] = "b5c6d7e8f9a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transcript_acquisitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column("raw_artifact_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=100), nullable=False),
        sa.Column("provider_version", sa.String(length=100), nullable=False),
        sa.Column("requested_location", sa.String(length=2048), nullable=False),
        sa.Column("resolved_location", sa.String(length=2048), nullable=True),
        sa.Column("external_identifier", sa.String(length=2048), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("language", sa.String(length=35), nullable=False),
        sa.Column(
            "transcript_kind",
            sa.Enum(
                "publisher_provided",
                "human_created",
                "auto_generated",
                "unknown",
                name="transcript_kind",
                native_enum=False,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column(
            "transcript_format",
            sa.Enum(
                "plain_text",
                "webvtt",
                "subrip",
                name="transcript_format",
                native_enum=False,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column("media_type", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=(
                "fk_transcript_acquisitions_document_version_id_"
                "document_versions"
            ),
        ),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id"],
            ["raw_artifacts.id"],
            name=(
                "fk_transcript_acquisitions_raw_artifact_id_raw_artifacts"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id",
            "raw_artifact_id",
            "provider",
            "provider_version",
            "requested_location",
            "retrieved_at",
            "language",
            "transcript_kind",
            "transcript_format",
            name="uq_transcript_acquisition_provenance",
        ),
    )
    for column in (
        "document_version_id",
        "raw_artifact_id",
        "provider",
        "external_identifier",
        "retrieved_at",
        "language",
        "transcript_kind",
        "transcript_format",
    ):
        op.create_index(
            op.f(f"ix_transcript_acquisitions_{column}"),
            "transcript_acquisitions",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in reversed((
        "document_version_id",
        "raw_artifact_id",
        "provider",
        "external_identifier",
        "retrieved_at",
        "language",
        "transcript_kind",
        "transcript_format",
    )):
        op.drop_index(
            op.f(f"ix_transcript_acquisitions_{column}"),
            table_name="transcript_acquisitions",
        )
    op.drop_table("transcript_acquisitions")
