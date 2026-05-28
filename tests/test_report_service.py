"""Unit tests for ReportService — uses mocked DB session."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.report_service import MonthlyReportData, ReportService, StockReportLine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stock(
    sid: int,
    ticker: str,
    name: str,
    currency: str = "EUR",
) -> MagicMock:
    stock = MagicMock()
    stock.id = sid
    stock.ticker = ticker
    stock.name = name
    stock.currency = currency
    return stock


def _make_db(stocks: list[MagicMock], price_rows: list[tuple]) -> AsyncMock:  # type: ignore[type-arg]
    """Return an AsyncMock db whose execute() returns stocks on the first call
    and price rows on the second call."""
    db = AsyncMock()

    stocks_result = MagicMock()
    stocks_result.scalars.return_value.all.return_value = stocks

    prices_result = MagicMock()
    prices_result.__iter__ = MagicMock(return_value=iter(price_rows))

    db.execute = AsyncMock(side_effect=[stocks_result, prices_result])
    return db


def _patch_positions(
    start: dict[int, str],
    end: dict[int, str],
) -> MagicMock:
    """Return a patch context for net_shares_as_of_date.

    *start* and *end* map stock_id → quantity string for the two calls made
    by _build_report (start-of-period and end-of-period).
    """
    return patch(
        "app.services.report_service.net_shares_as_of_date",
        new=AsyncMock(
            side_effect=[
                {k: Decimal(v) for k, v in start.items()},
                {k: Decimal(v) for k, v in end.items()},
            ]
        ),
    )


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
    with patch(
        "app.services.report_service.net_shares_as_of_date",
        new=AsyncMock(return_value={}),
    ):
        report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is None


@pytest.mark.asyncio
async def test_single_holding_full_data() -> None:
    stocks = [_make_stock(1, "AAPL", "Apple Inc.")]
    price_rows = [
        ("AAPL", _MAR_1, Decimal("150.0000")),
        ("AAPL", _MAR_31, Decimal("160.0000")),
    ]
    db = _make_db(stocks, price_rows)

    with _patch_positions(start={1: "10"}, end={1: "10"}):
        report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is not None
    assert report.month_label == "March 2026"
    assert report.period_start == _MAR_1
    assert report.period_end == _MAR_31

    assert len(report.lines) == 1
    line = report.lines[0]
    assert line.ticker == "AAPL"
    assert line.quantity == Decimal("10")
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
    stocks = [_make_stock(1, "TSLA", "Tesla Inc.")]
    price_rows = [
        ("TSLA", _MAR_1, Decimal("200.0000")),
        ("TSLA", _MAR_31, Decimal("180.0000")),
    ]
    db = _make_db(stocks, price_rows)

    with _patch_positions(start={1: "5"}, end={1: "5"}):
        report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is not None
    line = report.lines[0]
    assert line.delta_eur == Decimal("-100.0000")
    assert line.delta_pct == Decimal("-10.00")
    assert report.total_delta_eur == Decimal("-100.0000")


@pytest.mark.asyncio
async def test_holding_without_cached_prices() -> None:
    stocks = [_make_stock(1, "UNKN", "Unknown Corp.")]
    db = _make_db(stocks, [])  # no price rows

    with _patch_positions(start={1: "3"}, end={1: "3"}):
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
    stocks = [
        _make_stock(1, "AAPL", "Apple Inc."),
        _make_stock(2, "UNKN", "Unknown Corp."),
    ]
    price_rows = [
        ("AAPL", _MAR_1, Decimal("100.0000")),
        ("AAPL", _MAR_31, Decimal("110.0000")),
    ]
    db = _make_db(stocks, price_rows)

    with _patch_positions(start={1: "10", 2: "5"}, end={1: "10", 2: "5"}):
        report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is not None
    assert len(report.lines) == 2

    aapl = next(line for line in report.lines if line.ticker == "AAPL")
    unkn = next(line for line in report.lines if line.ticker == "UNKN")

    assert aapl.delta_eur == Decimal("100.0000")
    assert unkn.delta_eur is None

    # Totals are based only on AAPL
    assert report.total_value_1st == Decimal("1000.0000")
    assert report.total_value_last == Decimal("1100.0000")
    assert report.total_delta_eur == Decimal("100.0000")


@pytest.mark.asyncio
async def test_uses_first_and_last_available_trading_day() -> None:
    """When prices don't start on the 1st, use the earliest/latest available."""
    stocks = [_make_stock(1, "MSFT", "Microsoft Corp.")]
    mar_3 = datetime.date(2026, 3, 3)
    mar_28 = datetime.date(2026, 3, 28)
    price_rows = [
        ("MSFT", mar_3, Decimal("400.0000")),
        ("MSFT", mar_28, Decimal("420.0000")),
    ]
    db = _make_db(stocks, price_rows)

    with _patch_positions(start={1: "2"}, end={1: "2"}):
        report = await ReportService().generate_monthly_report(db, reference_date=_REF)

    assert report is not None
    line = report.lines[0]
    assert line.price_1st == Decimal("400.0000")
    assert line.price_last == Decimal("420.0000")


@pytest.mark.asyncio
async def test_mid_month_purchase_uses_historical_quantities() -> None:
    """Shares bought mid-month: value_1st uses start quantity, value_last uses end quantity."""
    stocks = [_make_stock(1, "AAPL", "Apple Inc.")]
    price_rows = [
        ("AAPL", _MAR_1, Decimal("100.0000")),
        ("AAPL", _MAR_31, Decimal("110.0000")),
    ]
    db = _make_db(stocks, price_rows)

    with _patch_positions(start={1: "5"}, end={1: "15"}):
        report = await ReportService()._build_report(db, _MAR_1, _MAR_31)

    assert report is not None
    line = report.lines[0]
    assert line.quantity == Decimal("15")
    assert line.value_1st == Decimal("500.0000")    # 5 × 100
    assert line.value_last == Decimal("1650.0000")  # 15 × 110
    assert line.delta_eur == Decimal("1150.0000")
    assert line.delta_pct == Decimal("230.00")      # 1150/500 × 100


@pytest.mark.asyncio
async def test_usd_stock_fx_conversion() -> None:
    """USD stock prices are converted to EUR before computing values."""
    stocks = [_make_stock(1, "AAPL", "Apple Inc.", currency="USD")]
    price_rows = [
        ("AAPL", _MAR_1, Decimal("110.0000")),
        ("AAPL", _MAR_31, Decimal("110.0000")),
    ]
    db = _make_db(stocks, price_rows)

    # 1 EUR = 1.10 USD → $110 = €100
    def fake_to_eur(amount: Decimal, currency: str) -> Decimal:
        return amount / Decimal("1.1") if currency == "USD" else amount

    with _patch_positions(start={1: "10"}, end={1: "10"}), patch(
        "app.services.report_service.to_eur", side_effect=fake_to_eur
    ):
        report = await ReportService()._build_report(db, _MAR_1, _MAR_31)

    assert report is not None
    line = report.lines[0]
    expected_price = Decimal("110.0000") / Decimal("1.1")  # = 100
    assert line.price_1st == expected_price
    assert line.price_last == expected_price
    assert line.value_1st == Decimal("10") * expected_price
    assert line.delta_eur == Decimal("0")


# ---------------------------------------------------------------------------
# Tests: render_html
# ---------------------------------------------------------------------------

def _make_report_data() -> MonthlyReportData:
    lines = [
        StockReportLine(
            ticker="AAPL",
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
            ticker="TSLA",
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
    assert "AAPL" in html
    assert "Apple Inc." in html
    assert "1500.00" in html
    assert "1600.00" in html
    assert "+€100.00" in html
    assert "+6.67%" in html
    assert "TSLA" in html


def test_render_html_shows_dash_for_missing_prices() -> None:
    data = _make_report_data()
    html = ReportService().render_html(data)

    # TSLA has no prices — dashes should appear
    assert "—" in html


def test_render_html_negative_delta_no_plus_sign() -> None:
    lines = [
        StockReportLine(
            ticker="TSLA",
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
