"""add current_price to stock

Revision ID: b5e8d2f3a1c6
Revises: a3f7c9e1b2d4
Create Date: 2026-04-06 00:01:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b5e8d2f3a1c6"
down_revision: str | None = "a3f7c9e1b2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stock",
        sa.Column("current_price", sa.Numeric(precision=18, scale=4), nullable=True),
        schema="finance",
    )


def downgrade() -> None:
    op.drop_column("stock", "current_price", schema="finance")
