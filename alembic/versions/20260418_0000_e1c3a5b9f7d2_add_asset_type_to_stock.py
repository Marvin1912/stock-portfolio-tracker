"""add asset_type to stock

Revision ID: e1c3a5b9f7d2
Revises: d9b2f6a4e8c1
Create Date: 2026-04-18 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1c3a5b9f7d2"
down_revision: str | None = "d9b2f6a4e8c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stock",
        sa.Column(
            "asset_type",
            sa.String(length=16),
            nullable=False,
            server_default="STOCK",
        ),
        schema="finance",
    )
    op.alter_column("stock", "asset_type", server_default=None, schema="finance")


def downgrade() -> None:
    op.drop_column("stock", "asset_type", schema="finance")
