"""create fx_rate table

Revision ID: d9b2f6a4e8c1
Revises: c7a1e4f8b3d5
Create Date: 2026-04-17 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9b2f6a4e8c1"
down_revision: str | None = "c7a1e4f8b3d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fx_rate",
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("rate", sa.Numeric(precision=18, scale=8), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("currency"),
        schema="finance",
    )


def downgrade() -> None:
    op.drop_table("fx_rate", schema="finance")
