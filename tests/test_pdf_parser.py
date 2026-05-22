"""Tests for the GenericTableParser and ImportService."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.generic_parser import GenericTableParser
from app.services.import_service import ImportService

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "sample_holdings.pdf"


# ---------------------------------------------------------------------------
# GenericTableParser – pure unit tests (no DB required)
# ---------------------------------------------------------------------------


def test_generic_parser_extracts_all_rows() -> None:
    parser = GenericTableParser()
    holdings = parser.extract(FIXTURE_PDF)
    assert len(holdings) == 3


def test_generic_parser_tickers_are_uppercase() -> None:
    parser = GenericTableParser()
    for ticker, _ in parser.extract(FIXTURE_PDF):
        assert ticker == ticker.upper()
        assert ticker.strip() == ticker


def test_generic_parser_quantities() -> None:
    parser = GenericTableParser()
    by_ticker = dict(parser.extract(FIXTURE_PDF))
    assert by_ticker["AAPL"] == Decimal("10.00000000")
    assert by_ticker["MSFT"] == Decimal("5.50000000")
    assert by_ticker["GOOGL"] == Decimal("2.75000000")


def test_generic_parser_ignores_header_row() -> None:
    """The 'Ticker  Quantity' header line must not appear in results."""
    parser = GenericTableParser()
    tickers = {t for t, _ in parser.extract(FIXTURE_PDF)}
    assert "Ticker" not in tickers
    assert "TICKER" not in tickers


# ---------------------------------------------------------------------------
# ImportService – upsert logic (async mocks, no DB required)
# ---------------------------------------------------------------------------


def _make_db(known_tickers: dict[str, int]) -> AsyncMock:
    """Mock DB that resolves ``known_tickers`` to Stock rows and otherwise
    returns empty results — enough to exercise ImportService end-to-end,
    including the recompute_holdings tail call.
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()

    async def _execute(stmt):  # type: ignore[no-untyped-def]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        result = MagicMock()

        if "external_uuid" in compiled:
            result.scalar_one_or_none.return_value = None
            return result

        if "stock.ticker" in compiled or 'stock"."ticker' in compiled:
            ticker = next((t for t in known_tickers if f"'{t}'" in compiled), None)
            if ticker:
                stock = MagicMock()
                stock.id = known_tickers[ticker]
                stock.ticker = ticker
                stock.currency = "EUR"
                result.scalar_one_or_none.return_value = stock
            else:
                result.scalar_one_or_none.return_value = None
            return result

        result.all.return_value = []
        result.scalars.return_value.all.return_value = []
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = AsyncMock(side_effect=_execute)
    return db


@pytest.mark.asyncio
async def test_import_writes_transaction_for_known_ticker() -> None:
    db = _make_db({"AAPL": 42})

    parser = MagicMock()
    parser.extract.return_value = [("AAPL", Decimal("5"))]

    service = ImportService()
    result = await service.import_from_pdf(FIXTURE_PDF, parser, db)

    assert result == [("AAPL", Decimal("5"))]
    added_tx = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "Transaction"
    ]
    assert len(added_tx) == 1
    tx = added_tx[0]
    assert tx.stock_id == 42
    assert tx.shares == Decimal("5")
    assert tx.type == "BUY"
    assert tx.source == "PDF"


@pytest.mark.asyncio
async def test_import_is_idempotent_across_two_runs() -> None:
    """Re-importing the same PDF must not create duplicate transactions."""
    inserted_uuids: set[str] = set()

    db = AsyncMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()

    async def _execute(stmt):  # type: ignore[no-untyped-def]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        result = MagicMock()

        if "external_uuid" in compiled:
            seen = next((u for u in inserted_uuids if u in compiled), None)
            result.scalar_one_or_none.return_value = 1 if seen else None
            return result

        if "stock.ticker" in compiled or 'stock"."ticker' in compiled:
            stock = MagicMock()
            stock.id = 7
            stock.ticker = "MSFT"
            stock.currency = "EUR"
            result.scalar_one_or_none.return_value = stock
            return result

        result.all.return_value = []
        result.scalars.return_value.all.return_value = []
        result.scalar_one_or_none.return_value = None
        return result

    def _add(obj):  # type: ignore[no-untyped-def]
        if obj.__class__.__name__ == "Transaction":
            inserted_uuids.add(obj.external_uuid)

    db.execute = AsyncMock(side_effect=_execute)
    db.add = MagicMock(side_effect=_add)

    parser = MagicMock()
    parser.extract.return_value = [("MSFT", Decimal("2.5"))]

    service = ImportService()
    first = await service.import_from_pdf(FIXTURE_PDF, parser, db)
    db.add.reset_mock()
    second = await service.import_from_pdf(FIXTURE_PDF, parser, db)

    assert first == [("MSFT", Decimal("2.5"))]
    assert second == [("MSFT", Decimal("2.5"))]
    added_tx_second = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "Transaction"
    ]
    assert added_tx_second == []


@pytest.mark.asyncio
async def test_import_skips_unknown_ticker() -> None:
    db = _make_db({})

    parser = MagicMock()
    parser.extract.return_value = [("UNKNOWN", Decimal("1"))]

    service = ImportService()
    result = await service.import_from_pdf(FIXTURE_PDF, parser, db)

    assert result == []
    added_tx = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "Transaction"
    ]
    assert added_tx == []
