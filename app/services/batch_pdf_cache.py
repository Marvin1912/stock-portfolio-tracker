"""In-memory store that bridges the multi-PDF batch preview and confirm steps."""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from app.services.comdirect_parser import ParsedTrade

_TTL_SECONDS = 30 * 60


@dataclass(slots=True)
class BatchPdfItem:
    filename: str
    trade: ParsedTrade | None  # comdirect trade, or None
    ticker: str | None  # resolved ticker, or None
    is_duplicate: bool | None  # True/False, or None when ticker unknown
    pairs: list[tuple[str, Decimal]] | None  # generic holdings, or None
    parse_error: str | None  # error message if the file could not be parsed


@dataclass(slots=True)
class BatchPdfPreview:
    items: list[BatchPdfItem]
    created_at: float = field(default_factory=time.monotonic)


_store: dict[str, BatchPdfPreview] = {}
_lock = threading.Lock()


def store(items: list[BatchPdfItem]) -> str:
    token = uuid.uuid4().hex
    with _lock:
        _evict_expired_locked()
        _store[token] = BatchPdfPreview(items=items)
    return token


def get(token: str) -> BatchPdfPreview | None:
    with _lock:
        _evict_expired_locked()
        return _store.get(token)


def delete(token: str) -> None:
    with _lock:
        _store.pop(token, None)


def _evict_expired_locked() -> None:
    now = time.monotonic()
    expired = [k for k, e in _store.items() if now - e.created_at > _TTL_SECONDS]
    for k in expired:
        _store.pop(k, None)
