"""In-memory store that bridges the XML preview and confirm steps.

The preview step parses the XML and resolves each security against Yahoo
Finance — work we do not want to repeat at confirm time, and the manual
ticker overrides the user enters in the preview need somewhere to live
until they click Confirm.  A single-process FastAPI app is enough here;
no Redis required.

Entries expire 30 minutes after their last touch (lazy eviction on read).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

from app.services.portfolio_performance_importer import ParseResult
from app.services.xml_security_resolver import ResolvedSecurity

_TTL_SECONDS = 30 * 60


@dataclass(slots=True)
class ImportPreviewEntry:
    parse_result: ParseResult
    resolutions: dict[str, ResolvedSecurity]  # keyed by security UUID
    filename: str
    created_at: float = field(default_factory=time.monotonic)


_store: dict[str, ImportPreviewEntry] = {}
_lock = threading.Lock()


def store(entry: ImportPreviewEntry) -> str:
    token = uuid.uuid4().hex
    with _lock:
        _evict_expired_locked()
        _store[token] = entry
    return token


def get(token: str) -> ImportPreviewEntry | None:
    with _lock:
        _evict_expired_locked()
        return _store.get(token)


def update_resolution(token: str, security_uuid: str, resolution: ResolvedSecurity) -> bool:
    with _lock:
        _evict_expired_locked()
        entry = _store.get(token)
        if entry is None:
            return False
        entry.resolutions[security_uuid] = resolution
        entry.created_at = time.monotonic()  # refresh TTL on activity
        return True


def delete(token: str) -> None:
    with _lock:
        _store.pop(token, None)


def _evict_expired_locked() -> None:
    now = time.monotonic()
    expired = [k for k, e in _store.items() if now - e.created_at > _TTL_SECONDS]
    for k in expired:
        _store.pop(k, None)
