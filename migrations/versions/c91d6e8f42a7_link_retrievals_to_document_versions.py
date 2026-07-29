"""link retrieval attempts to document versions

Revision ID: c91d6e8f42a7
Revises: 7f3a1c9d2e4b
Create Date: 2026-07-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c91d6e8f42a7"
down_revision: Union[str, Sequence[str], None] = "7f3a1c9d2e4b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("retrieval_attempts") as batch_op:
        batch_op.add_column(
            sa.Column(
                "document_version_id",
                sa.Integer(),
                nullable=True,
            )
        )
        batch_op.create_index(
            batch_op.f(
                "ix_retrieval_attempts_document_version_id"
            ),
            ["document_version_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_retrieval_attempts_document_version_id_"
            "document_versions",
            "document_versions",
            ["document_version_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("retrieval_attempts") as batch_op:
        batch_op.drop_constraint(
            "fk_retrieval_attempts_document_version_id_"
            "document_versions",
            type_="foreignkey",
        )
        batch_op.drop_index(
            batch_op.f(
                "ix_retrieval_attempts_document_version_id"
            )
        )
        batch_op.drop_column("document_version_id")
