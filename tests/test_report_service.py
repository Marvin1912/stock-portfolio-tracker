"""Unit tests for ReportService — uses mocked DB session."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.report_service import MonthlyReportData, ReportService, StockReportLine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_holding(
    wkn: str,
    ticker: str,
    name: str,
    quantity: str,
) -> MagicMock:
    stock = MagicMock()
    stock.wkn = wkn
    stock.ticker = ticker
    stock.name = name

    holding = MagicMock()
    holding.quantity = Decimal(quantity)
    holding.stock = stock
    return holding


def _make_db(holdings: list[MagicMock], price_rows: list[tuple]) -> AsyncMock:  # type: ignore[type-arg]
    """Return an AsyncMock db whose execute() returns holdings on the first call
    and price rows on the second call."""
    db = AsyncMock()

    holdings_result = MagicMock()
    holdings_result.scalars.return_value.all.return_value = holdings

    prices_result = MagicMock()
    prices_result.__iter__ = MagicMock(return_value=iter(price_rows))

    db.execute = AsyncMock(side_effect=[holdings_result, prices_result])
    return db


# Reference date: 10 Apr 2026 → previous month is March 2026
_REF = datetime.date(2026, 4, 10)
_MAR_1 = datetime.date(2026, 3, 1)
_MAR_31 = datetime.date(2026, 3, 31)


# ---------------------------------------------------------------------------
# Tests: generate_monthly_report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_holdings_returns_none() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)

    report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is None


@pytest.mark.asyncio
async def test_single_holding_full_data() -> None:
    holdings = [_make_holding("AAPL01", "AAPL", "Apple Inc.", "10")]
    price_rows = [
        ("AAPL", _MAR_1, Decimal("150.0000")),
        ("AAPL", _MAR_31, Decimal("160.0000")),
    ]
    db = _make_db(holdings, price_rows)

    report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is not None
    assert report.month_label == "March 2026"
    assert report.period_start == _MAR_1
    assert report.period_end == _MAR_31

    assert len(report.lines) == 1
    line = report.lines[0]
    assert line.wkn == "AAPL01"
    assert line.price_1st == Decimal("150.0000")
    assert line.price_last == Decimal("160.0000")
    assert line.value_1st == Decimal("1500.0000")
    assert line.value_last == Decimal("1600.0000")
    assert line.delta_eur == Decimal("100.0000")
    assert line.delta_pct == Decimal("6.67")

    assert report.total_value_1st == Decimal("1500.0000")
    assert report.total_value_last == Decimal("1600.0000")
    assert report.total_delta_eur == Decimal("100.0000")
    assert report.total_delta_pct == Decimal("6.67")


@pytest.mark.asyncio
async def test_negative_delta() -> None:
    holdings = [_make_holding("TSLA01", "TSLA", "Tesla Inc.", "5")]
    price_rows = [
        ("TSLA", _MAR_1, Decimal("200.0000")),
        ("TSLA", _MAR_31, Decimal("180.0000")),
    ]
    db = _make_db(holdings, price_rows)

    report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is not None
    line = report.lines[0]
    assert line.delta_eur == Decimal("-100.0000")
    assert line.delta_pct == Decimal("-10.00")
    assert report.total_delta_eur == Decimal("-100.0000")


@pytest.mark.asyncio
async def test_holding_without_cached_prices() -> None:
    holdings = [_make_holding("UNKN01", "UNKN", "Unknown Corp.", "3")]
    db = _make_db(holdings, [])  # no price rows

    report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is not None
    line = report.lines[0]
    assert line.price_1st is None
    assert line.price_last is None
    assert line.value_1st is None
    assert line.value_last is None
    assert line.delta_eur is None
    assert line.delta_pct is None
    assert report.total_value_1st is None
    assert report.total_value_last is None
    assert report.total_delta_eur is None


@pytest.mark.asyncio
async def test_multiple_holdings_mixed_prices() -> None:
    """Holdings with and without cached prices; total excludes missing prices."""
    holdings = [
        _make_holding("AAPL01", "AAPL", "Apple Inc.", "10"),
        _make_holding("UNKN01", "UNKN", "Unknown Corp.", "5"),
    ]
    price_rows = [
        ("AAPL", _MAR_1, Decimal("100.0000")),
        ("AAPL", _MAR_31, Decimal("110.0000")),
    ]
    db = _make_db(holdings, price_rows)

    report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is not None
    assert len(report.lines) == 2

    aapl = next(line for line in report.lines if line.wkn == "AAPL01")
    unkn = next(line for line in report.lines if line.wkn == "UNKN01")

    assert aapl.delta_eur == Decimal("100.0000")
    assert unkn.delta_eur is None

    # Totals are based only on AAPL
    assert report.total_value_1st == Decimal("1000.0000")
    assert report.total_value_last == Decimal("1100.0000")
    assert report.total_delta_eur == Decimal("100.0000")


@pytest.mark.asyncio
async def test_uses_first_and_last_available_trading_day() -> None:
    """When prices don't start on the 1st, use the earliest/latest available."""
    holdings = [_make_holding("MSFT01", "MSFT", "Microsoft Corp.", "2")]
    mar_3 = datetime.date(2026, 3, 3)
    mar_28 = datetime.date(2026, 3, 28)
    price_rows = [
        ("MSFT", mar_3, Decimal("400.0000")),
        ("MSFT", mar_28, Decimal("420.0000")),
    ]
    db = _make_db(holdings, price_rows)

    report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is not None
    line = report.lines[0]
    assert line.price_1st == Decimal("400.0000")
    assert line.price_last == Decimal("420.0000")


# ---------------------------------------------------------------------------
# Tests: render_html
# ---------------------------------------------------------------------------

def _make_report_data() -> MonthlyReportData:
    lines = [
        StockReportLine(
            wkn="AAPL01",
            name="Apple Inc.",
            quantity=Decimal("10"),
            price_1st=Decimal("150.00"),
            price_last=Decimal("160.00"),
            value_1st=Decimal("1500.00"),
            value_last=Decimal("1600.00"),
            delta_eur=Decimal("100.00"),
            delta_pct=Decimal("6.67"),
        ),
        StockReportLine(
            wkn="TSLA01",
            name="Tesla Inc.",
            quantity=Decimal("5"),
            price_1st=None,
            price_last=None,
            value_1st=None,
            value_last=None,
            delta_eur=None,
            delta_pct=None,
        ),
    ]
    return MonthlyReportData(
        month_label="March 2026",
        period_start=datetime.date(2026, 3, 1),
        period_end=datetime.date(2026, 3, 31),
        lines=lines,
        total_value_1st=Decimal("1500.00"),
        total_value_last=Decimal("1600.00"),
        total_delta_eur=Decimal("100.00"),
        total_delta_pct=Decimal("6.67"),
    )


def test_render_html_contains_key_data() -> None:
    data = _make_report_data()
    html = ReportService().render_html(data)

    assert "March 2026" in html
    assert "AAPL01" in html
    assert "Apple Inc." in html
    assert "1500.00" in html
    assert "1600.00" in html
    assert "+€100.00" in html
    assert "+6.67%" in html
    assert "TSLA01" in html


def test_render_html_shows_dash_for_missing_prices() -> None:
    data = _make_report_data()
    html = ReportService().render_html(data)

    # TSLA01 has no prices — dashes should appear
    assert "—" in html


def test_render_html_negative_delta_no_plus_sign() -> None:
    lines = [
        StockReportLine(
            wkn="TSLA01",
            name="Tesla Inc.",
            quantity=Decimal("5"),
            price_1st=Decimal("200.00"),
            price_last=Decimal("180.00"),
            value_1st=Decimal("1000.00"),
            value_last=Decimal("900.00"),
            delta_eur=Decimal("-100.00"),
            delta_pct=Decimal("-10.00"),
        )
    ]
    data = MonthlyReportData(
        month_label="March 2026",
        period_start=datetime.date(2026, 3, 1),
        period_end=datetime.date(2026, 3, 31),
        lines=lines,
        total_value_1st=Decimal("1000.00"),
        total_value_last=Decimal("900.00"),
        total_delta_eur=Decimal("-100.00"),
        total_delta_pct=Decimal("-10.00"),
    )
    html = ReportService().render_html(data)

    assert "€-100.00" in html or "-€100.00" in html or "-100.00" in html
    assert "+€-100.00" not in html
