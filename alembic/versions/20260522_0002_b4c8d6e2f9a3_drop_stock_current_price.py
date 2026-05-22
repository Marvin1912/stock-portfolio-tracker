"""drop stock.current_price

Revision ID: b4c8d6e2f9a3
Revises: a8b4e2c6d9f1
Create Date: 2026-05-22 00:00:02.000000

The column was a stale snapshot written once at stock creation.  The
PriceCache table is the single source of truth for prices, so the
column has been removed (issue #100).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b4c8d6e2f9a3"
down_revision: str | None = "a8b4e2c6d9f1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("stock", "current_price", schema="finance")


def downgrade() -> None:
    op.add_column(
        "stock",
        sa.Column("current_price", sa.Numeric(precision=18, scale=4), nullable=True),
        schema="finance",
    )
