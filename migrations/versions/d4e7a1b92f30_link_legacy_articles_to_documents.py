"""link legacy articles to documents

Revision ID: d4e7a1b92f30
Revises: c91d6e8f42a7
Create Date: 2026-07-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e7a1b92f30"
down_revision: Union[str, Sequence[str], None] = "c91d6e8f42a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("articles") as batch_op:
        batch_op.add_column(
            sa.Column("document_id", sa.Integer(), nullable=True)
        )

    conflicting_document = op.get_bind().execute(
        sa.text(
            """
            SELECT documents.id
            FROM articles
            JOIN documents
              ON documents.identifier_scheme = 'uri'
             AND documents.identifier_value = articles.url
            WHERE documents.document_type != 'article'
            LIMIT 1
            """
        )
    ).first()

    if conflicting_document is not None:
        raise RuntimeError(
            "Cannot link a legacy article to a non-article document."
        )

    op.execute(
        sa.text(
            """
            INSERT INTO documents (
                source_id,
                document_type,
                identifier_scheme,
                identifier_value,
                title,
                language,
                created_at,
                updated_at
            )
            SELECT
                articles.source_id,
                'article',
                'uri',
                articles.url,
                articles.title,
                articles.language,
                CURRENT_TIMESTAMP,
                CURRENT_TIMESTAMP
            FROM articles
            WHERE NOT EXISTS (
                SELECT 1
                FROM documents
                WHERE documents.identifier_scheme = 'uri'
                  AND documents.identifier_value = articles.url
            )
            """
        )
    )

    op.execute(
        sa.text(
            """
            UPDATE articles
            SET document_id = (
                SELECT documents.id
                FROM documents
                WHERE documents.identifier_scheme = 'uri'
                  AND documents.identifier_value = articles.url
            )
            WHERE document_id IS NULL
            """
        )
    )

    with op.batch_alter_table("articles") as batch_op:
        batch_op.create_index(
            batch_op.f("ix_articles_document_id"),
            ["document_id"],
            unique=False,
        )
        batch_op.create_unique_constraint(
            "uq_articles_document_id",
            ["document_id"],
        )
        batch_op.create_foreign_key(
            "fk_articles_document_id_documents",
            "documents",
            ["document_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("articles") as batch_op:
        batch_op.drop_constraint(
            "fk_articles_document_id_documents",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "uq_articles_document_id",
            type_="unique",
        )
        batch_op.drop_index(
            batch_op.f("ix_articles_document_id")
        )
        batch_op.drop_column("document_id")
