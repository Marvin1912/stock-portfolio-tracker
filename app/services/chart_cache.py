"""Lightweight in-memory TTL cache for chart computation results.

Chart data (performance, gain/loss, allocation) is expensive to compute but
changes only when new transactions are imported or the daily price refresh
runs.  This module caches those results for up to _TTL seconds and exposes
an invalidate() function so the import flow can bust the cache immediately
after committing new transactions.
"""

from __future__ import annotations

import time
from typing import Any

_TTL = 300  # seconds — matches the Cache-Control max-age on chart endpoints
_store: dict[str, tuple[float, Any]] = {}


def get(key: str) -> Any | None:
    """Return the cached value for *key* if it is still within TTL, else None."""
    entry = _store.get(key)
    if entry is not None and (time.monotonic() - entry[0]) < _TTL:
        return entry[1]
    return None


def set(key: str, value: Any) -> None:  # noqa: A001
    """Store *value* under *key* with the current timestamp."""
    _store[key] = (time.monotonic(), value)


def invalidate() -> None:
    """Clear all cached chart results (call after importing new transactions)."""
    _store.clear()
