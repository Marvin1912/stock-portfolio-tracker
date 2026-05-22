"""Tests for the clear-xml-import admin flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.services.import_cleanup import CleanupSummary, clear_xml_imports


def _result(rowcount: int = 0, rows: list | None = None) -> MagicMock:
    result = MagicMock()
    result.rowcount = rowcount
    result.all.return_value = rows or []
    return result


@pytest.mark.asyncio
async def test_clear_xml_imports_returns_counts_and_recomputes_holdings() -> None:
    """Service deletes XML tx, runs recompute_holdings, drops orphan stocks."""
    db = MagicMock()

    # 1) select(stock_id distinct) → two affected stocks
    # 2) delete(Transaction) → 5 rows deleted
    # 3) delete(Stock) → 2 orphans deleted
    db.execute = AsyncMock(
        side_effect=[
            _result(rows=[(10,), (20,)]),
            _result(rowcount=5),
            _result(rowcount=2),
        ]
    )
    db.flush = AsyncMock()

    with patch(
        "app.services.import_cleanup.recompute_holdings",
        new=AsyncMock(),
    ) as mock_recompute:
        summary = await clear_xml_imports(db)

    assert summary == CleanupSummary(deleted_transactions=5, deleted_stocks=2)
    mock_recompute.assert_awaited_once()
    args, _ = mock_recompute.call_args
    assert args[0] is db
    assert args[1] == {10, 20}
    db.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_xml_imports_noop_when_no_xml_transactions() -> None:
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _result(rows=[]),       # no affected stocks
            _result(rowcount=0),    # no transactions to delete
        ]
    )
    db.flush = AsyncMock()

    with patch(
        "app.services.import_cleanup.recompute_holdings",
        new=AsyncMock(),
    ) as mock_recompute:
        summary = await clear_xml_imports(db)

    assert summary.deleted_transactions == 0
    assert summary.deleted_stocks == 0
    mock_recompute.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_clear_xml_endpoint_returns_summary_fragment(
    client: AsyncClient,
) -> None:
    """POST /admin/clear-xml-import returns an HTMX fragment with the counts."""
    with patch(
        "app.routers.admin.clear_xml_imports",
        new=AsyncMock(return_value=CleanupSummary(deleted_transactions=7, deleted_stocks=3)),
    ):
        response = await client.post("/admin/clear-xml-import")

    assert response.status_code == 200
    assert "Cleared 7 XML transaction" in response.text
    assert "3 orphaned stock" in response.text
