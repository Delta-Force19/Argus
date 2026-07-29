"""link article content to derived text

Revision ID: f6a8d3c91b42
Revises: e8b4c2d71f06
Create Date: 2026-07-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a8d3c91b42"
down_revision: Union[str, Sequence[str], None] = "e8b4c2d71f06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("articles") as batch_op:
        batch_op.add_column(
            sa.Column(
                "content_derived_artifact_id",
                sa.Integer(),
                nullable=True,
            )
        )
        batch_op.create_index(
            batch_op.f(
                "ix_articles_content_derived_artifact_id"
            ),
            ["content_derived_artifact_id"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_articles_content_derived_artifact_id",
            ["content_derived_artifact_id"],
        )
        batch_op.create_foreign_key(
            (
                "fk_articles_content_derived_artifact_id_"
                "derived_artifacts"
            ),
            "derived_artifacts",
            ["content_derived_artifact_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("articles") as batch_op:
        batch_op.drop_constraint(
            (
                "fk_articles_content_derived_artifact_id_"
                "derived_artifacts"
            ),
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "uq_articles_content_derived_artifact_id",
            type_="unique",
        )
        batch_op.drop_index(
            batch_op.f(
                "ix_articles_content_derived_artifact_id"
            )
        )
        batch_op.drop_column("content_derived_artifact_id")
