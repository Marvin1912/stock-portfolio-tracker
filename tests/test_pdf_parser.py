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


def test_generic_parser_wkns_are_uppercase() -> None:
    parser = GenericTableParser()
    for wkn, _ in parser.extract(FIXTURE_PDF):
        assert wkn == wkn.upper()
        assert wkn.strip() == wkn


def test_generic_parser_quantities() -> None:
    parser = GenericTableParser()
    by_wkn = dict(parser.extract(FIXTURE_PDF))
    assert by_wkn["865985"] == Decimal("10.00000000")
    assert by_wkn["870747"] == Decimal("5.50000000")
    assert by_wkn["A14Y6F"] == Decimal("2.75000000")


def test_generic_parser_ignores_header_row() -> None:
    """The 'WKN  Quantity' header line must not appear in results."""
    parser = GenericTableParser()
    wkns = {w for w, _ in parser.extract(FIXTURE_PDF)}
    assert "WKN" not in wkns


# ---------------------------------------------------------------------------
# ImportService – upsert logic (async mocks, no DB required)
# ---------------------------------------------------------------------------


def _make_stock(wkn: str, stock_id: int = 1) -> MagicMock:
    stock = MagicMock()
    stock.id = stock_id
    stock.wkn = wkn
    return stock


def _make_holding(stock_id: int, quantity: Decimal) -> MagicMock:
    holding = MagicMock()
    holding.stock_id = stock_id
    holding.quantity = quantity
    return holding


def _db_with(stock: MagicMock | None, holding: MagicMock | None) -> AsyncMock:
    """Build a minimal async DB session mock returning *stock* and *holding*."""
    db = AsyncMock()
    db.add = MagicMock()  # db.add is synchronous in SQLAlchemy

    stock_result = MagicMock()
    stock_result.scalar_one_or_none.return_value = stock

    holding_result = MagicMock()
    holding_result.scalar_one_or_none.return_value = holding

    db.execute = AsyncMock(side_effect=[stock_result, holding_result])
    return db


@pytest.mark.asyncio
async def test_import_creates_new_holding_when_none_exists() -> None:
    stock = _make_stock("AAPL", stock_id=42)
    db = _db_with(stock=stock, holding=None)

    parser = MagicMock()
    parser.extract.return_value = [("AAPL", Decimal("5"))]

    service = ImportService()
    result = await service.import_from_pdf(FIXTURE_PDF, parser, db)

    assert result == [("AAPL", Decimal("5"))]
    db.add.assert_called_once()
    added: MagicMock = db.add.call_args[0][0]
    assert added.stock_id == 42
    assert added.quantity == Decimal("5")


@pytest.mark.asyncio
async def test_import_increases_existing_holding() -> None:
    stock = _make_stock("MSFT", stock_id=7)
    holding = _make_holding(stock_id=7, quantity=Decimal("10"))
    db = _db_with(stock=stock, holding=holding)

    parser = MagicMock()
    parser.extract.return_value = [("MSFT", Decimal("2.5"))]

    service = ImportService()
    result = await service.import_from_pdf(FIXTURE_PDF, parser, db)

    assert result == [("MSFT", Decimal("2.5"))]
    assert holding.quantity == Decimal("12.5")
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_import_skips_unknown_ticker() -> None:
    db = AsyncMock()
    no_stock = MagicMock()
    no_stock.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=no_stock)

    parser = MagicMock()
    parser.extract.return_value = [("UNKNOWN", Decimal("1"))]

    service = ImportService()
    result = await service.import_from_pdf(FIXTURE_PDF, parser, db)

    assert result == []
    db.add.assert_not_called()
