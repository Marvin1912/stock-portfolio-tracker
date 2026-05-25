"""Tests for the ComdirectParser and ImportService.import_trade."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import comdirect_parser as cp_mod
from app.services.comdirect_parser import ComdirectParser, ParsedTrade
from app.services.import_service import ImportService


@pytest.fixture(autouse=True)
def _stub_price_warmup():
    """Keep import_trade unit tests offline: the post-import price-cache warmup
    would otherwise call yfinance for each freshly created ticker."""
    with patch(
        "app.services.import_service.ensure_prices_cached",
        new=AsyncMock(return_value=[]),
    ):
        yield

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "sample_comdirect_kauf.pdf"

# A faithful copy of the text pdfplumber extracts from a comdirect buy
# settlement, used to exercise parse_text without a PDF round-trip.
KAUF_TEXT = """\
GESCHÄFTSABRECHNUNG VOM 23.03.2026
Wertpapierkauf
Ordernummer : 000512215771-001 Rechnungsnummer : 701341243997D195
Geschäftstag : 23.03.2026 Ausführungsplatz : TRADEGATE
Wertpapier-Bezeichnung WPKNR/ISIN
Xtr.(IE) - MSCI World A1XB5U
Registered Shares 1C o.N. IE00BJ0KDQ92
Nennwert Zum Kurs von
St. 8 EUR 117,5406
Kurswert : EUR 940,32
Summe Entgelte : EUR 15,30
EUR 25.03.2026 EUR 955,62
Ihre comdirect
"""


# ---------------------------------------------------------------------------
# ComdirectParser – pure unit tests (no DB required)
# ---------------------------------------------------------------------------


def test_matches_recognises_comdirect_buy() -> None:
    assert ComdirectParser.matches(KAUF_TEXT) is True


def test_matches_rejects_unrelated_pdf() -> None:
    assert ComdirectParser.matches("AAPL 10.0\nMSFT 5.5") is False
    # comdirect-branded but not a securities trade → not our format.
    assert ComdirectParser.matches("Ihre comdirect\nKontoauszug") is False


def test_parse_text_extracts_all_fields() -> None:
    trade = ComdirectParser().parse_text(KAUF_TEXT)
    assert trade is not None
    assert trade.trade_type == "BUY"
    assert trade.name == "Xtr.(IE) - MSCI World Registered Shares 1C o.N."
    assert trade.wkn == "A1XB5U"
    assert trade.isin == "IE00BJ0KDQ92"
    assert trade.shares == Decimal("8")
    assert trade.price == Decimal("117.5406")
    assert trade.amount == Decimal("940.32")
    assert trade.fee == Decimal("15.30")
    assert trade.tax == Decimal("0")
    assert trade.currency == "EUR"
    assert trade.date == datetime.datetime(2026, 3, 23, tzinfo=datetime.UTC)
    assert trade.order_ref == "000512215771-001"


def test_extract_trade_from_fixture_pdf() -> None:
    trade = ComdirectParser().extract_trade(FIXTURE_PDF)
    assert trade is not None
    assert trade.wkn == "A1XB5U"
    assert trade.isin == "IE00BJ0KDQ92"
    assert trade.shares == Decimal("8")
    assert trade.amount == Decimal("940.32")
    assert trade.fee == Decimal("15.30")


def test_parse_text_detects_sell() -> None:
    trade = ComdirectParser().parse_text(KAUF_TEXT.replace("Wertpapierkauf", "Wertpapierverkauf"))
    assert trade is not None
    assert trade.trade_type == "SELL"


def test_parse_text_handles_thousands_separator() -> None:
    text = (
        "Wertpapierkauf\n"
        "Wertpapier-Bezeichnung WPKNR/ISIN\n"
        "Some Big Fund AG 766403\n"
        "Inhaber-Aktien DE0007664039\n"
        "Nennwert Zum Kurs von\n"
        "St. 100 EUR 1.234,5600\n"
        "Kurswert : EUR 123.456,00\n"
        "Summe Entgelte : EUR 1.000,00\n"
        "Ihre comdirect\n"
    )
    trade = ComdirectParser().parse_text(text)
    assert trade is not None
    assert trade.shares == Decimal("100")
    assert trade.price == Decimal("1234.5600")
    assert trade.amount == Decimal("123456.00")
    assert trade.fee == Decimal("1000.00")
    assert trade.wkn == "766403"
    assert trade.isin == "DE0007664039"


def test_parse_text_returns_none_for_non_comdirect() -> None:
    assert ComdirectParser().parse_text("AAPL 10.0\nMSFT 5.5") is None


# ---------------------------------------------------------------------------
# extract_trade – fast (pypdf) path with pdfplumber fallback
# ---------------------------------------------------------------------------


def test_extract_trade_uses_fast_path_without_pdfplumber(monkeypatch) -> None:
    """When pypdf yields parseable text, pdfplumber must not run."""
    fast = MagicMock(return_value=[KAUF_TEXT])
    robust = MagicMock(side_effect=AssertionError("pdfplumber should not be called"))
    monkeypatch.setattr(cp_mod, "extract_pages_fast", fast)
    monkeypatch.setattr(cp_mod, "extract_pages_robust", robust)

    trade = ComdirectParser().extract_trade(Path("ignored.pdf"))

    assert trade is not None and trade.wkn == "A1XB5U"
    fast.assert_called_once()


def test_extract_trade_falls_back_when_fast_text_unparseable(monkeypatch) -> None:
    """comdirect-looking text that lacks the fields triggers the pdfplumber retry."""
    partial = "Wertpapierkauf\nIhre comdirect\n"  # matches() True, no fields
    fast = MagicMock(return_value=[partial])
    robust = MagicMock(return_value=[KAUF_TEXT])
    monkeypatch.setattr(cp_mod, "extract_pages_fast", fast)
    monkeypatch.setattr(cp_mod, "extract_pages_robust", robust)

    trade = ComdirectParser().extract_trade(Path("ignored.pdf"))

    assert trade is not None and trade.wkn == "A1XB5U"
    robust.assert_called_once()


def test_extract_trade_skips_fallback_for_non_comdirect(monkeypatch) -> None:
    """A non-comdirect PDF must not pay the slow pdfplumber path."""
    fast = MagicMock(return_value=["AAPL 10.0\nMSFT 5.5"])
    robust = MagicMock(side_effect=AssertionError("pdfplumber should not be called"))
    monkeypatch.setattr(cp_mod, "extract_pages_fast", fast)
    monkeypatch.setattr(cp_mod, "extract_pages_robust", robust)

    assert ComdirectParser().extract_trade(Path("ignored.pdf")) is None


def test_extract_trade_falls_back_when_pypdf_raises(monkeypatch) -> None:
    """If pypdf errors out, pdfplumber still produces the trade."""
    fast = MagicMock(side_effect=RuntimeError("corrupt stream"))
    robust = MagicMock(return_value=[KAUF_TEXT])
    monkeypatch.setattr(cp_mod, "extract_pages_fast", fast)
    monkeypatch.setattr(cp_mod, "extract_pages_robust", robust)

    trade = ComdirectParser().extract_trade(Path("ignored.pdf"))

    assert trade is not None and trade.wkn == "A1XB5U"
    robust.assert_called_once()


# ---------------------------------------------------------------------------
# ImportService.import_trade – upsert logic (async mocks, no DB required)
# ---------------------------------------------------------------------------


def _make_db(
    known_tickers: dict[str, int],
    *,
    duplicate_exists: bool = False,
    existing_external_uuids: set[str] | None = None,
) -> AsyncMock:
    existing_external_uuids = existing_external_uuids or set()
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()

    async def _execute(stmt):  # type: ignore[no-untyped-def]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        result = MagicMock()

        # The fuzzy fallback probe is the only query that filters on
        # transaction.date — distinguish it from the recompute_holdings queries.
        if "transaction.date" in compiled or 'transaction"."date"' in compiled:
            result.scalar_one_or_none.return_value = 99 if duplicate_exists else None
            return result

        # Exact external_uuid lookup — the order-ref dedupe (issue #114).
        if "external_uuid" in compiled:
            found = next(
                (u for u in existing_external_uuids if u in compiled), None
            )
            result.scalar_one_or_none.return_value = 1 if found else None
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


def _trade(
    *,
    order_ref: str | None = "000512215771-001",
    shares: str = "8",
) -> ParsedTrade:
    return ParsedTrade(
        trade_type="BUY",
        name="Xtr.(IE) - MSCI World",
        wkn="A1XB5U",
        isin="IE00BJ0KDQ92",
        shares=Decimal(shares),
        price=Decimal("117.5406"),
        amount=Decimal("940.32"),
        fee=Decimal("15.30"),
        tax=Decimal("0"),
        currency="EUR",
        date=datetime.datetime(2026, 3, 23, tzinfo=datetime.UTC),
        order_ref=order_ref,
    )


@pytest.mark.asyncio
async def test_import_trade_writes_full_transaction() -> None:
    db = _make_db({"XDWD.DE": 7})

    status = await ImportService().import_trade(_trade(), "XDWD.DE", db)

    assert status == "created"
    added = [
        c.args[0]
        for c in db.add.call_args_list
        if c.args[0].__class__.__name__ == "Transaction"
    ]
    assert len(added) == 1
    tx = added[0]
    assert tx.stock_id == 7
    assert tx.type == "BUY"
    assert tx.shares == Decimal("8")
    assert tx.amount == Decimal("940.32")
    assert tx.fee == Decimal("15.30")
    assert tx.tax == Decimal("0")
    assert tx.source == "PDF"
    assert tx.date == datetime.datetime(2026, 3, 23, tzinfo=datetime.UTC)
    assert tx.external_uuid == "pdf:comdirect:000512215771-001"


@pytest.mark.asyncio
async def test_import_trade_warms_price_cache_on_create() -> None:
    """A newly created trade triggers a price-cache warmup for its ticker."""
    db = _make_db({"XDWD.DE": 7})

    with patch(
        "app.services.import_service.ensure_prices_cached",
        new=AsyncMock(return_value=["XDWD.DE"]),
    ) as mock_ensure:
        status = await ImportService().import_trade(_trade(), "XDWD.DE", db)

    assert status == "created"
    mock_ensure.assert_awaited_once()
    assert list(mock_ensure.call_args.args[0]) == ["XDWD.DE"]


@pytest.mark.asyncio
async def test_import_trade_skips_unknown_ticker() -> None:
    db = _make_db({})

    status = await ImportService().import_trade(_trade(), "UNKNOWN", db)

    assert status == "unknown_ticker"
    added = [
        c.args[0]
        for c in db.add.call_args_list
        if c.args[0].__class__.__name__ == "Transaction"
    ]
    assert added == []


@pytest.mark.asyncio
async def test_import_trade_skips_cross_source_duplicate_by_exact_key() -> None:
    """A trade already imported from XML under the shared comdirect key must not
    be re-inserted — the exact ``pdf:comdirect:{ref}`` lookup catches it, so no
    IntegrityError on the unique constraint (issue #114)."""
    db = _make_db(
        {"XDWD.DE": 7},
        existing_external_uuids={"pdf:comdirect:000512215771-001"},
    )

    status = await ImportService().import_trade(_trade(), "XDWD.DE", db)

    assert status == "duplicate"
    added = [
        c.args[0]
        for c in db.add.call_args_list
        if c.args[0].__class__.__name__ == "Transaction"
    ]
    assert added == []


@pytest.mark.asyncio
async def test_import_trade_distinct_order_refs_both_insert() -> None:
    """Two distinct same-day, same-share comdirect trades with *different*
    order refs both get distinct keys and both insert (issue #114)."""
    db = _make_db({"XDWD.DE": 7})

    first = await ImportService().import_trade(
        _trade(order_ref="000512215771-001"), "XDWD.DE", db
    )
    second = await ImportService().import_trade(
        _trade(order_ref="000512215771-002"), "XDWD.DE", db
    )

    assert first == "created"
    assert second == "created"
    keys = {
        c.args[0].external_uuid
        for c in db.add.call_args_list
        if c.args[0].__class__.__name__ == "Transaction"
    }
    assert keys == {
        "pdf:comdirect:000512215771-001",
        "pdf:comdirect:000512215771-002",
    }


@pytest.mark.asyncio
async def test_import_trade_without_order_ref_uses_fuzzy_fallback() -> None:
    """A ref-less trade still dedupes via the fuzzy same-day probe."""
    db = _make_db({"XDWD.DE": 7}, duplicate_exists=True)

    status = await ImportService().import_trade(
        _trade(order_ref=None), "XDWD.DE", db
    )

    assert status == "duplicate"
    added = [
        c.args[0]
        for c in db.add.call_args_list
        if c.args[0].__class__.__name__ == "Transaction"
    ]
    assert added == []
