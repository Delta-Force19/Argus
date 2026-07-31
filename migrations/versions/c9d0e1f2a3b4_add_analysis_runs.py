"""add reproducible analysis runs

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analysis_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_version_id", sa.Integer(), nullable=False),
        sa.Column("entity_type_scope", sa.String(length=50), nullable=False),
        sa.Column("analysis_method", sa.String(length=255), nullable=False),
        sa.Column(
            "analysis_method_version",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column("software_version", sa.String(length=100), nullable=False),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "input_schema_version",
            sa.String(length=100),
            nullable=False,
        ),
        sa.Column("input_manifest", sa.JSON(), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            "length(configuration_hash) = 64",
            name="ck_analysis_runs_configuration_hash_sha256",
        ),
        sa.CheckConstraint(
            "length(input_fingerprint) = 64",
            name="ck_analysis_runs_input_fingerprint_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=(
                "fk_analysis_runs_document_version_id_"
                "document_versions"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "input_fingerprint",
            "analysis_method",
            "analysis_method_version",
            "software_version",
            "configuration_hash",
            name="uq_analysis_runs_reproducible_preparation",
        ),
    )
    for column in (
        "document_version_id",
        "entity_type_scope",
        "analysis_method",
        "configuration_hash",
        "input_fingerprint",
        "status",
    ):
        op.create_index(
            op.f(f"ix_analysis_runs_{column}"),
            "analysis_runs",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in reversed((
        "document_version_id",
        "entity_type_scope",
        "analysis_method",
        "configuration_hash",
        "input_fingerprint",
        "status",
    )):
        op.drop_index(
            op.f(f"ix_analysis_runs_{column}"),
            table_name="analysis_runs",
        )
    op.drop_table("analysis_runs")
