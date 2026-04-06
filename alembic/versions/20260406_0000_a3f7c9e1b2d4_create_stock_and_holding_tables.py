"""create stock and holding tables

Revision ID: a3f7c9e1b2d4
Revises:
Create Date: 2026-04-06 00:00:00.000000

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f7c9e1b2d4"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS costs")

    op.create_table(
        "stock",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", name="uq_stock_ticker"),
        schema="costs",
    )

    op.create_table(
        "holding",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.ForeignKeyConstraint(
            ["stock_id"],
            ["costs.stock.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="costs",
    )


def downgrade() -> None:
    op.drop_table("holding", schema="costs")
    op.drop_table("stock", schema="costs")
    op.execute("DROP SCHEMA IF EXISTS costs")
