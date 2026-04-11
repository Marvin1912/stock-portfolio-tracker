"""remove ticker — use WKN as sole stock identifier

Revision ID: e3c6f7a2b9d1
Revises: d2b9e5f1a8c4
Create Date: 2026-04-11 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3c6f7a2b9d1"
down_revision: str | None = "d2b9e5f1a8c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Drop ticker unique constraint and column from stock table.
    op.drop_constraint("uq_stock_ticker", "stock", schema="costs", type_="unique")
    op.drop_column("stock", "ticker", schema="costs")

    # Rename price_cache.ticker → price_cache.wkn and update the unique constraint.
    op.drop_constraint(
        "uq_price_cache_ticker_date", "price_cache", schema="costs", type_="unique"
    )
    op.alter_column(
        "price_cache",
        "ticker",
        new_column_name="wkn",
        schema="costs",
    )
    op.create_unique_constraint(
        "uq_price_cache_wkn_date", "price_cache", ["wkn", "date"], schema="costs"
    )


def downgrade() -> None:
    # Revert price_cache changes.
    op.drop_constraint(
        "uq_price_cache_wkn_date", "price_cache", schema="costs", type_="unique"
    )
    op.alter_column(
        "price_cache",
        "wkn",
        new_column_name="ticker",
        schema="costs",
    )
    op.create_unique_constraint(
        "uq_price_cache_ticker_date", "price_cache", ["ticker", "date"], schema="costs"
    )

    # Restore ticker column on stock (nullable, no data restored).
    op.add_column(
        "stock",
        sa.Column("ticker", sa.String(length=20), nullable=True),
        schema="costs",
    )
    op.create_unique_constraint("uq_stock_ticker", "stock", ["ticker"], schema="costs")
