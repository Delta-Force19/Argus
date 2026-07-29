"""add documents and document versions

Revision ID: 7f3a1c9d2e4b
Revises: b22c36e940db
Create Date: 2026-07-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f3a1c9d2e4b"
down_revision: Union[str, Sequence[str], None] = "b22c36e940db"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column(
            "document_type",
            sa.Enum(
                "article",
                "report",
                "law",
                "speech",
                "scientific_work",
                "dataset",
                "historical_record",
                "other",
                name="document_type",
                native_enum=False,
                length=50,
            ),
            nullable=False,
        ),
        sa.Column("identifier_scheme", sa.String(length=100), nullable=False),
        sa.Column("identifier_value", sa.String(length=2048), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=35), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["sources.id"],
            name="fk_documents_source_id_sources",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "identifier_scheme",
            "identifier_value",
            name="uq_document_stable_identity",
        ),
    )
    op.create_index(
        op.f("ix_documents_document_type"),
        "documents",
        ["document_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_documents_identifier_scheme"),
        "documents",
        ["identifier_scheme"],
        unique=False,
    )
    op.create_index(
        op.f("ix_documents_language"),
        "documents",
        ["language"],
        unique=False,
    )
    op.create_index(
        op.f("ix_documents_source_id"),
        "documents",
        ["source_id"],
        unique=False,
    )

    op.create_table(
        "document_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("raw_artifact_id", sa.Integer(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_document_versions_number_positive",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_document_versions_document_id_documents",
        ),
        sa.ForeignKeyConstraint(
            ["raw_artifact_id"],
            ["raw_artifacts.id"],
            name=(
                "fk_document_versions_raw_artifact_id_raw_artifacts"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "raw_artifact_id",
            name="uq_document_version_artifact",
        ),
        sa.UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_document_version_number",
        ),
    )
    op.create_index(
        op.f("ix_document_versions_document_id"),
        "document_versions",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_versions_published_at"),
        "document_versions",
        ["published_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_document_versions_raw_artifact_id"),
        "document_versions",
        ["raw_artifact_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("document_versions")
    op.drop_table("documents")
