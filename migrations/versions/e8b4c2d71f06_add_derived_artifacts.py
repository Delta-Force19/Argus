"""add derived artifacts

Revision ID: e8b4c2d71f06
Revises: d4e7a1b92f30
Create Date: 2026-07-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e8b4c2d71f06"
down_revision: Union[str, Sequence[str], None] = "d4e7a1b92f30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "derived_artifacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column("artifact_type", sa.String(length=50), nullable=False),
        sa.Column("method", sa.String(length=255), nullable=False),
        sa.Column("method_version", sa.String(length=100), nullable=False),
        sa.Column("schema_version", sa.String(length=100), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("quality_limitations", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=(
                "fk_derived_artifacts_document_version_id_"
                "document_versions"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_version_id",
            "artifact_type",
            "method",
            "method_version",
            "schema_version",
            "content_hash",
            name="uq_derived_artifact_reproducible_output",
        ),
    )
    op.create_index(
        op.f("ix_derived_artifacts_artifact_type"),
        "derived_artifacts",
        ["artifact_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_derived_artifacts_content_hash"),
        "derived_artifacts",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_derived_artifacts_document_version_id"),
        "derived_artifacts",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_derived_artifacts_method"),
        "derived_artifacts",
        ["method"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_derived_artifacts_method"),
        table_name="derived_artifacts",
    )
    op.drop_index(
        op.f("ix_derived_artifacts_document_version_id"),
        table_name="derived_artifacts",
    )
    op.drop_index(
        op.f("ix_derived_artifacts_content_hash"),
        table_name="derived_artifacts",
    )
    op.drop_index(
        op.f("ix_derived_artifacts_artifact_type"),
        table_name="derived_artifacts",
    )
    op.drop_table("derived_artifacts")
