"""create price_cache table

Revision ID: c7a1e4f8b3d5
Revises: b5e8d2f3a1c6
Create Date: 2026-04-06 00:02:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c7a1e4f8b3d5"
down_revision: str | None = "b5e8d2f3a1c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_cache",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("close_price", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ticker", "date", name="uq_price_cache_ticker_date"),
        schema="costs",
    )


def downgrade() -> None:
    op.drop_table("price_cache", schema="costs")
