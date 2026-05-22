"""Tests for the XML preview in-memory cache."""

from __future__ import annotations

import time
from unittest.mock import patch

from app.services import import_cache
from app.services.import_cache import ImportPreviewEntry
from app.services.portfolio_performance_importer import ParseResult


def _entry() -> ImportPreviewEntry:
    return ImportPreviewEntry(
        parse_result=ParseResult(
            version="69",
            base_currency="EUR",
            transactions=[],
            securities={},
        ),
        resolutions={},
        filename="x.xml",
    )


def test_store_and_get_returns_same_entry() -> None:
    entry = _entry()
    token = import_cache.store(entry)
    assert import_cache.get(token) is entry


def test_get_returns_none_for_unknown_token() -> None:
    assert import_cache.get("not-a-token") is None


def test_delete_removes_entry() -> None:
    token = import_cache.store(_entry())
    import_cache.delete(token)
    assert import_cache.get(token) is None


def test_expired_entries_are_evicted_on_read() -> None:
    token = import_cache.store(_entry())
    # Fast-forward beyond the 30-min TTL.
    with patch.object(time, "monotonic", return_value=time.monotonic() + 60 * 60):
        assert import_cache.get(token) is None


def test_update_resolution_refreshes_ttl_and_replaces_entry() -> None:
    from app.services.xml_security_resolver import ResolvedSecurity

    entry = _entry()
    entry.resolutions["u1"] = ResolvedSecurity(
        uuid="u1",
        original_ticker="X",
        original_name="X",
        isin=None,
        status="needs_attention",
        resolved_ticker=None,
        asset_type="STOCK",
        suggestion_source="manual",
        yahoo_name=None,
        currency="EUR",
    )
    token = import_cache.store(entry)

    fixed = ResolvedSecurity(
        uuid="u1",
        original_ticker="X",
        original_name="X",
        isin=None,
        status="valid",
        resolved_ticker="X.DE",
        asset_type="STOCK",
        suggestion_source="manual",
        yahoo_name="X AG",
        currency="EUR",
    )
    assert import_cache.update_resolution(token, "u1", fixed) is True
    assert import_cache.get(token).resolutions["u1"].status == "valid"


def test_update_resolution_returns_false_for_unknown_token() -> None:
    from app.services.xml_security_resolver import ResolvedSecurity

    res = ResolvedSecurity(
        uuid="x",
        original_ticker=None,
        original_name=None,
        isin=None,
        status="valid",
        resolved_ticker="X",
        asset_type="STOCK",
        suggestion_source="manual",
        yahoo_name=None,
        currency=None,
    )
    assert import_cache.update_resolution("missing", "x", res) is False
