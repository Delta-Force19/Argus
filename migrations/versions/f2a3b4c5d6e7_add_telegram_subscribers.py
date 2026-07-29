"""add telegram subscribers

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-07-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f2a3b4c5d6e7"
down_revision: Union[str, Sequence[str], None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_subscribers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("is_subscribed", sa.Boolean(), nullable=False),
        sa.Column(
            "last_delivered_article_id",
            sa.Integer(),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint(
            (
                "last_delivered_article_id IS NULL "
                "OR last_delivered_article_id >= 0"
            ),
            name=(
                "ck_telegram_subscribers_last_article_id_non_negative"
            ),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_telegram_subscribers_chat_id"),
        "telegram_subscribers",
        ["chat_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_telegram_subscribers_status"),
        "telegram_subscribers",
        ["status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_subscribers_is_subscribed"),
        "telegram_subscribers",
        ["is_subscribed"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_telegram_subscribers_is_subscribed"),
        table_name="telegram_subscribers",
    )
    op.drop_index(
        op.f("ix_telegram_subscribers_status"),
        table_name="telegram_subscribers",
    )
    op.drop_index(
        op.f("ix_telegram_subscribers_chat_id"),
        table_name="telegram_subscribers",
    )
    op.drop_table("telegram_subscribers")
