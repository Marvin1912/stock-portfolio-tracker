"""Tests for the IngParser and its ImportService integration."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import ing_parser as ing_mod
from app.services.import_service import ImportService
from app.services.ing_parser import IngParser
from tests.test_comdirect_parser import _make_db


@pytest.fixture(autouse=True)
def _stub_price_warmup():
    """Keep import_trade unit tests offline: the post-import price-cache warmup
    would otherwise call yfinance for each freshly created ticker."""
    with patch(
        "app.services.import_service.ensure_prices_cached",
        new=AsyncMock(return_value=[]),
    ):
        yield


FIXTURE_PDF = Path(__file__).parent / "fixtures" / "sample_ing_kauf.pdf"

# A faithful copy of the text pypdf extracts from an ING buy settlement, used to
# exercise parse_text without a PDF round-trip.
KAUF_TEXT = """\
Wertpapierabrechnung Kauf
Ordernummer 456480204.001
ISIN (WKN) IE00B4L5Y983 (A0RPWH)
Wertpapierbezeichnung iShsIII-Core MSCI World U.ETF
Registered Shs USD (Acc) o.N.
Nominale Stück 9,00
Kurs EUR 107,5157
Handelsplatz Direkthandel
Ausführungstag / -zeit 23.03.2026 um 07:33:17 Uhr
Kurswert EUR 967,64
Provision EUR 7,32
Endbetrag zu Ihren Lasten EUR 974,96
Valuta 25.03.2026
ING-DiBa AG · 60628 Frankfurt am Main
"""


# ---------------------------------------------------------------------------
# IngParser – pure unit tests (no DB required)
# ---------------------------------------------------------------------------


def test_matches_recognises_ing_settlement() -> None:
    assert IngParser.matches(KAUF_TEXT) is True


def test_matches_rejects_unrelated_pdf() -> None:
    assert IngParser.matches("AAPL 10.0\nMSFT 5.5") is False
    # ING-branded but not a securities settlement → not our format.
    assert IngParser.matches("ING-DiBa AG\nKontoauszug") is False


def test_parse_text_extracts_all_fields() -> None:
    trade = IngParser().parse_text(KAUF_TEXT)
    assert trade is not None
    assert trade.trade_type == "BUY"
    assert trade.name == "iShsIII-Core MSCI World U.ETF Registered Shs USD (Acc) o.N."
    assert trade.wkn == "A0RPWH"
    assert trade.isin == "IE00B4L5Y983"
    assert trade.shares == Decimal("9.00")
    assert trade.price == Decimal("107.5157")
    assert trade.amount == Decimal("967.64")
    assert trade.fee == Decimal("7.32")
    assert trade.tax == Decimal("0")
    assert trade.currency == "EUR"
    assert trade.date == datetime.datetime(2026, 3, 23, tzinfo=datetime.UTC)
    assert trade.order_ref == "456480204.001"
    assert trade.broker == "ing"


def test_extract_trade_from_fixture_pdf() -> None:
    trade = IngParser().extract_trade(FIXTURE_PDF)
    assert trade is not None
    assert trade.wkn == "A0RPWH"
    assert trade.isin == "IE00B4L5Y983"
    assert trade.shares == Decimal("9.00")
    assert trade.amount == Decimal("967.64")
    assert trade.fee == Decimal("7.32")
    assert trade.broker == "ing"


def test_parse_text_detects_sell() -> None:
    sell_text = KAUF_TEXT.replace("Kauf", "Verkauf").replace(
        "zu Ihren Lasten", "zu Ihren Gunsten"
    )
    trade = IngParser().parse_text(sell_text)
    assert trade is not None
    assert trade.trade_type == "SELL"


def test_parse_text_returns_none_for_non_ing() -> None:
    assert IngParser().parse_text("AAPL 10.0\nMSFT 5.5") is None


# ---------------------------------------------------------------------------
# extract_trade – fast (pypdf) path with pdfplumber fallback
# ---------------------------------------------------------------------------


def test_extract_trade_uses_fast_path_without_pdfplumber(monkeypatch) -> None:
    """When pypdf yields parseable text, pdfplumber must not run."""
    fast = MagicMock(return_value=[KAUF_TEXT])
    robust = MagicMock(side_effect=AssertionError("pdfplumber should not be called"))
    monkeypatch.setattr(ing_mod, "extract_pages_fast", fast)
    monkeypatch.setattr(ing_mod, "extract_pages_robust", robust)

    trade = IngParser().extract_trade(Path("ignored.pdf"))

    assert trade is not None and trade.wkn == "A0RPWH"
    fast.assert_called_once()


def test_extract_trade_skips_fallback_for_non_ing(monkeypatch) -> None:
    """A non-ING PDF must not pay the slow pdfplumber path."""
    fast = MagicMock(return_value=["AAPL 10.0\nMSFT 5.5"])
    robust = MagicMock(side_effect=AssertionError("pdfplumber should not be called"))
    monkeypatch.setattr(ing_mod, "extract_pages_fast", fast)
    monkeypatch.setattr(ing_mod, "extract_pages_robust", robust)

    assert IngParser().extract_trade(Path("ignored.pdf")) is None


# ---------------------------------------------------------------------------
# ImportService.import_trade – ING order-number cross-source dedupe
# ---------------------------------------------------------------------------

ORDER_KEY = "pdf:ing:456480204.001"


def _ing_trade():
    return IngParser().parse_text(KAUF_TEXT)


@pytest.mark.asyncio
async def test_import_trade_writes_order_ref_key() -> None:
    """ING trades are keyed by pdf:ing:{order_ref}, bridging against the
    matching PP/XML transaction whose note carries the same Ordernummer."""
    db = _make_db({"IWDA.AS": 7})

    status = await ImportService().import_trade(_ing_trade(), "IWDA.AS", db)

    assert status == "created"
    added = [
        c.args[0]
        for c in db.add.call_args_list
        if c.args[0].__class__.__name__ == "Transaction"
    ]
    assert len(added) == 1
    assert added[0].external_uuid == ORDER_KEY
    assert added[0].amount == Decimal("967.64")
    assert added[0].fee == Decimal("7.32")


@pytest.mark.asyncio
async def test_import_trade_skips_duplicate_by_order_ref_key() -> None:
    """A trade already present under the order-ref key (e.g. imported earlier
    from a PP/XML export) is detected as a duplicate."""
    db = _make_db({"IWDA.AS": 7}, existing_external_uuids={ORDER_KEY})

    status = await ImportService().import_trade(_ing_trade(), "IWDA.AS", db)

    assert status == "duplicate"
    added = [
        c.args[0]
        for c in db.add.call_args_list
        if c.args[0].__class__.__name__ == "Transaction"
    ]
    assert added == []
