"""Tests for the ComdirectDividendParser."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.transaction import TX_TYPE_DIVIDEND
from app.services import comdirect_dividend_parser as cdp_mod
from app.services.comdirect_dividend_parser import ComdirectDividendParser
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


FIXTURE_PDF_USD = Path(__file__).parent / "fixtures" / "sample_comdirect_dividend_usd.pdf"
FIXTURE_PDF_EUR = Path(__file__).parent / "fixtures" / "sample_comdirect_dividend_eur.pdf"

# USD dividend with FX conversion and tax.
USD_DIVIDEND_TEXT = """\
comdirect bank – Dividendengutschrift
Referenz-Nr. : 1AINA2WQGJM0064Z
Geschaeftstag : 01.03.2026
per 15.03.2026 STRYKER CORP. 864765
STK 10 US8863161029 STRYKER CORP.
Depotbestand : 10
Quartalsdividende Bruttobetrag USD 1,00
Devisenkurs : 1,0950
Bruttobetrag EUR 10,95
Quellensteuer USD 0,15 (15%) = EUR 0,16
Nettobetrag EUR 10,79
Verrechnung ueber Konto Nr. ... 10,79
Valuta: 15.03.2026
zahlbar ab 15.03.2026 Quartalsdividende
"""

# EUR dividend with no tax.
EUR_DIVIDEND_TEXT = """\
comdirect bank – Dividendengutschrift
Referenz-Nr. : 2BJNA2WQGJM0065A
Geschaeftstag : 01.06.2026
per 15.06.2026 DEUTSCHE TELEKOM 555750
STK 20 DE0005557508 DEUTSCHE TELEKOM
Depotbestand : 20
Halbjahresdividende Bruttobetrag EUR 0,70
Bruttobetrag EUR 14,00
Nettobetrag EUR 14,00
Verrechnung ueber Konto Nr. ... 14,00
Valuta: 15.06.2026
zahlbar ab 15.06.2026 Halbjahresdividende
"""


# ---------------------------------------------------------------------------
# ComdirectDividendParser – pure unit tests (no DB required)
# ---------------------------------------------------------------------------


def test_matches_recognises_dividend() -> None:
    assert ComdirectDividendParser.matches(USD_DIVIDEND_TEXT) is True


def test_matches_rejects_trade() -> None:
    # A buy/sell statement, not a dividend.
    assert ComdirectDividendParser.matches(
        "comdirect\nWertpapierkauf\nOrdernummer : 000512215771-001"
    ) is False


def test_matches_rejects_unrelated_pdf() -> None:
    assert ComdirectDividendParser.matches("AAPL 10.0\nMSFT 5.5") is False


def test_parse_text_usd_dividend_extracts_all_fields() -> None:
    trade = ComdirectDividendParser().parse_text(USD_DIVIDEND_TEXT)
    assert trade is not None
    assert trade.trade_type == TX_TYPE_DIVIDEND
    assert trade.name == "STRYKER CORP."
    assert trade.wkn == "864765"
    assert trade.isin == "US8863161029"
    assert trade.shares == Decimal("10")
    assert trade.price is None
    assert trade.amount == Decimal("10.79")
    assert trade.fee == Decimal("0")
    # Tax: USD 0.15 * 1.0950 = EUR 0.16425
    assert trade.tax == Decimal("0.164250")
    assert trade.currency == "EUR"
    assert trade.date == datetime.datetime(2026, 3, 15, tzinfo=datetime.UTC)
    assert trade.order_ref == "1AINA2WQGJM0064Z"
    assert trade.note == "Ref.-Nr.: 1AINA2WQGJM0064Z | Quartalsdividende"


def test_parse_text_eur_dividend_extracts_all_fields() -> None:
    trade = ComdirectDividendParser().parse_text(EUR_DIVIDEND_TEXT)
    assert trade is not None
    assert trade.trade_type == TX_TYPE_DIVIDEND
    assert trade.name == "DEUTSCHE TELEKOM"
    assert trade.wkn == "555750"
    assert trade.isin == "DE0005557508"
    assert trade.shares == Decimal("20")
    assert trade.price is None
    assert trade.amount == Decimal("14.00")
    assert trade.fee == Decimal("0")
    assert trade.tax == Decimal("0")
    assert trade.currency == "EUR"
    assert trade.date == datetime.datetime(2026, 6, 15, tzinfo=datetime.UTC)
    assert trade.order_ref == "2BJNA2WQGJM0065A"
    assert trade.note == "Ref.-Nr.: 2BJNA2WQGJM0065A | Halbjahresdividende"


def test_parse_text_returns_none_for_non_dividend() -> None:
    assert ComdirectDividendParser().parse_text("AAPL 10.0\nMSFT 5.5") is None


def test_parse_text_returns_none_for_missing_amount() -> None:
    text = USD_DIVIDEND_TEXT.replace("Verrechnung ueber Konto Nr. ... 10,79", "")
    assert ComdirectDividendParser().parse_text(text) is None


def test_parse_text_returns_none_for_missing_isin_and_wkn() -> None:
    # Missing both ISIN and WKN should return None.
    text = USD_DIVIDEND_TEXT.replace("US8863161029", "").replace("864765", "")
    assert ComdirectDividendParser().parse_text(text) is None


# ---------------------------------------------------------------------------
# extract_trade – fast (pypdf) path with pdfplumber fallback
# ---------------------------------------------------------------------------


def test_extract_trade_uses_fast_path_without_pdfplumber(monkeypatch) -> None:
    """When pypdf yields parseable text, pdfplumber must not run."""
    fast = MagicMock(return_value=[USD_DIVIDEND_TEXT])
    robust = MagicMock(side_effect=AssertionError("pdfplumber should not be called"))
    monkeypatch.setattr(cdp_mod, "extract_pages_fast", fast)
    monkeypatch.setattr(cdp_mod, "extract_pages_robust", robust)

    trade = ComdirectDividendParser().extract_trade(Path("ignored.pdf"))

    assert trade is not None and trade.trade_type == TX_TYPE_DIVIDEND
    fast.assert_called_once()


def test_extract_trade_falls_back_when_fast_text_unparseable(monkeypatch) -> None:
    """dividend-looking text that lacks the fields triggers the pdfplumber retry."""
    partial = "comdirect\nDividendengutschrift\n"  # matches() True, no fields
    fast = MagicMock(return_value=[partial])
    robust = MagicMock(return_value=[USD_DIVIDEND_TEXT])
    monkeypatch.setattr(cdp_mod, "extract_pages_fast", fast)
    monkeypatch.setattr(cdp_mod, "extract_pages_robust", robust)

    trade = ComdirectDividendParser().extract_trade(Path("ignored.pdf"))

    assert trade is not None and trade.trade_type == TX_TYPE_DIVIDEND
    robust.assert_called_once()


def test_extract_trade_skips_fallback_for_non_comdirect(monkeypatch) -> None:
    """A non-comdirect PDF must not pay the slow pdfplumber path."""
    fast = MagicMock(return_value=["AAPL 10.0\nMSFT 5.5"])
    robust = MagicMock(side_effect=AssertionError("pdfplumber should not be called"))
    monkeypatch.setattr(cdp_mod, "extract_pages_fast", fast)
    monkeypatch.setattr(cdp_mod, "extract_pages_robust", robust)

    assert ComdirectDividendParser().extract_trade(Path("ignored.pdf")) is None


def test_extract_trade_falls_back_when_pypdf_raises(monkeypatch) -> None:
    """If pypdf errors out, pdfplumber still produces the dividend."""
    fast = MagicMock(side_effect=RuntimeError("corrupt stream"))
    robust = MagicMock(return_value=[USD_DIVIDEND_TEXT])
    monkeypatch.setattr(cdp_mod, "extract_pages_fast", fast)
    monkeypatch.setattr(cdp_mod, "extract_pages_robust", robust)

    trade = ComdirectDividendParser().extract_trade(Path("ignored.pdf"))

    assert trade is not None and trade.trade_type == TX_TYPE_DIVIDEND
    robust.assert_called_once()


def test_extract_trade_from_usd_fixture_pdf() -> None:
    trade = ComdirectDividendParser().extract_trade(FIXTURE_PDF_USD)
    assert trade is not None
    assert trade.trade_type == TX_TYPE_DIVIDEND
    assert trade.wkn == "864765"
    assert trade.isin == "US8863161029"
    assert trade.shares == Decimal("10")
    assert trade.amount == Decimal("10.79")


def test_extract_trade_from_eur_fixture_pdf() -> None:
    trade = ComdirectDividendParser().extract_trade(FIXTURE_PDF_EUR)
    assert trade is not None
    assert trade.trade_type == TX_TYPE_DIVIDEND
    assert trade.wkn == "555750"
    assert trade.isin == "DE0005557508"
    assert trade.shares == Decimal("20")
    assert trade.amount == Decimal("14.00")


# ---------------------------------------------------------------------------
# ImportService.import_trade – dividend upsert logic (async mocks, no DB required)
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

    async def _execute(stmt):  # type: ignore[no-untyped-def]
        result = MagicMock()
        result.scalar_one_or_none = MagicMock()
        if "external_uuid" in str(stmt):
            # Dedupe by external_uuid.
            result.scalar_one_or_none.return_value = (
                1 if any(
                    str(uuid) in str(stmt) for uuid in existing_external_uuids
                ) else None
            )
        elif "date" in str(stmt):
            # Fuzzy same-day duplicate check.
            result.scalar_one_or_none.return_value = 1 if duplicate_exists else None
        return result

    db.execute = _execute
    return db


@pytest.mark.asyncio
async def test_import_trade_dividend_creates_transaction() -> None:
    """import_trade should create a DIVIDEND transaction with the parsed note."""
    from app.models.stock import Stock

    trade = ComdirectDividendParser().parse_text(USD_DIVIDEND_TEXT)
    assert trade is not None

    db = _make_db({"SYK": 123})
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
    )

    # Mock Stock lookup.
    stock = MagicMock(spec=Stock)
    stock.id = 123
    stock.ticker = "SYK"
    stock.currency = "EUR"

    with patch.object(
        ImportService, "_get_stock", new_callable=AsyncMock
    ) as mock_get, patch.object(
        ImportService, "_uuid_exists", new_callable=AsyncMock
    ) as mock_exists:
        mock_get.return_value = stock
        mock_exists.return_value = False

        service = ImportService()
        status = await service.import_trade(trade, "SYK", db)

    assert status == "created"
    # Verify that the transaction was added with the correct note.
    db.add.assert_called_once()
    added_tx = db.add.call_args[0][0]
    assert added_tx.type == TX_TYPE_DIVIDEND
    assert added_tx.note == "Ref.-Nr.: 1AINA2WQGJM0064Z | Quartalsdividende"
    assert added_tx.amount == Decimal("10.79")
    assert added_tx.tax == Decimal("0.164250")


@pytest.mark.asyncio
async def test_import_trade_dividend_dedupes_on_replay() -> None:
    """Re-importing the same dividend (same order_ref) is a no-op."""
    from app.services.comdirect_ref import build_pdf_external_uuid

    trade = ComdirectDividendParser().parse_text(USD_DIVIDEND_TEXT)
    assert trade is not None

    existing_uuid = build_pdf_external_uuid("comdirect", trade.order_ref)

    db = _make_db({"SYK": 123}, existing_external_uuids={existing_uuid})
    db.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=1))
    )

    stock = MagicMock()
    stock.id = 123
    stock.ticker = "SYK"
    stock.currency = "EUR"

    with patch.object(
        ImportService, "_get_stock", new_callable=AsyncMock
    ) as mock_get, patch.object(
        ImportService, "_uuid_exists", new_callable=AsyncMock
    ) as mock_exists:
        mock_get.return_value = stock
        mock_exists.return_value = True

        service = ImportService()
        status = await service.import_trade(trade, "SYK", db)

    assert status == "duplicate"
    db.add.assert_not_called()
