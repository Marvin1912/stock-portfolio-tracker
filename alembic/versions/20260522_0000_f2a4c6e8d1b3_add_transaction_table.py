"""add transaction table

Revision ID: f2a4c6e8d1b3
Revises: e1c3a5b9f7d2
Create Date: 2026-05-22 00:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f2a4c6e8d1b3"
down_revision: str | None = "e1c3a5b9f7d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transaction",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("external_uuid", sa.String(length=128), nullable=True),
        sa.Column("stock_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("type", sa.String(length=16), nullable=False),
        sa.Column(
            "shares",
            sa.Numeric(precision=18, scale=8),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "amount",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "fee",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tax",
            sa.Numeric(precision=18, scale=2),
            nullable=False,
            server_default="0",
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["stock_id"],
            ["finance.stock.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_uuid", name="uq_transaction_external_uuid"),
        schema="finance",
    )
    op.create_index(
        "ix_transaction_stock_id_date",
        "transaction",
        ["stock_id", "date"],
        schema="finance",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_transaction_stock_id_date",
        table_name="transaction",
        schema="finance",
    )
    op.drop_table("transaction", schema="finance")
