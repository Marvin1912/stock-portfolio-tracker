"""add wkn to stock

Revision ID: d2b9e5f1a8c4
Revises: c7a1e4f8b3d5
Create Date: 2026-04-11 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2b9e5f1a8c4"
down_revision: str | None = "c7a1e4f8b3d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stock",
        sa.Column("wkn", sa.String(length=6), nullable=False, server_default=""),
        schema="costs",
    )
    # Remove the temporary server default after the column is added
    op.alter_column("stock", "wkn", server_default=None, schema="costs")
    op.create_unique_constraint("uq_stock_wkn", "stock", ["wkn"], schema="costs")


def downgrade() -> None:
    op.drop_constraint("uq_stock_wkn", "stock", schema="costs", type_="unique")
    op.drop_column("stock", "wkn", schema="costs")
