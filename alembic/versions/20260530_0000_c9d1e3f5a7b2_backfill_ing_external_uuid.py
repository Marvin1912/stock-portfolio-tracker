"""backfill ING external_uuid from PP uuid to pdf:ing:{order_ref}

Revision ID: c9d1e3f5a7b2
Revises: b4c8d6e2f9a3
Create Date: 2026-05-30 00:00:00.000000

Transactions imported from Portfolio Performance XML before PR #144 had the
ING Ordernummer in their note ("Ordernummer 376282589.001 | …") but were
stored under PP's random per-export UUID.  The new dedup logic keys on
"pdf:ing:{ref}", so those rows would never match — every re-import of the
same ING PDF or XML would be treated as new.

This migration extracts the order number from the note and rewrites the
external_uuid for affected rows so they are found by the new dedup logic.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9d1e3f5a7b2"
down_revision: str | None = "b4c8d6e2f9a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ING_ORDER_RE = re.compile(r"Ordernummer\s+([0-9][0-9.\-]*)")


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, note FROM finance.transaction "
            "WHERE note LIKE 'Ordernummer %' "
            "AND external_uuid NOT LIKE 'pdf:ing:%'"
        )
    ).fetchall()

    for row_id, note in rows:
        m = _ING_ORDER_RE.search(note)
        if m is None:
            continue
        new_uuid = f"pdf:ing:{m.group(1)}"
        conn.execute(
            sa.text(
                "UPDATE finance.transaction SET external_uuid = :uuid WHERE id = :id"
            ),
            {"uuid": new_uuid, "id": row_id},
        )


def downgrade() -> None:
    # Cannot recover the original PP UUIDs — a downgrade would leave these rows
    # with NULL external_uuid, breaking the unique constraint.  Guard against
    # accidental downgrades.
    raise NotImplementedError("Downgrade not supported for this migration.")
