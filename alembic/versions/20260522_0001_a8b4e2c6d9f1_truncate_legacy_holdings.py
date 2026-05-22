"""truncate legacy holdings

Revision ID: a8b4e2c6d9f1
Revises: f2a4c6e8d1b3
Create Date: 2026-05-22 00:00:01.000000

Destructive data migration.

After issue #98, ``finance.holding`` is a materialised projection of
``finance.transaction``.  Pre-existing holdings have no transaction
history backing them, so the old rows would silently disappear the
first time recompute runs.

We make that explicit here by truncating the table.  Users must
re-import their portfolio via the XML/PDF flows to repopulate it.

This migration has no schema effect — only data — so the downgrade
path is a no-op.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a8b4e2c6d9f1"
down_revision: str | None = "f2a4c6e8d1b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("TRUNCATE TABLE finance.holding RESTART IDENTITY CASCADE")


def downgrade() -> None:
    # Data-only migration: cannot restore truncated rows.
    pass
