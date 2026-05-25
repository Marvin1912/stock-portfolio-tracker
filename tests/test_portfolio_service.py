"""Unit tests for PortfolioService — uses mocked DB session."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.fx_service as fx_module
from app.services.portfolio_service import PortfolioService


def _make_holding(
    id: int,
    ticker: str,
    name: str,
    quantity: str,
    currency: str = "EUR",
    asset_type: str = "STOCK",
) -> MagicMock:
    stock = MagicMock()
    stock.ticker = ticker
    stock.name = name
    stock.currency = currency
    stock.asset_type = asset_type

    holding = MagicMock()
    holding.id = id
    holding.quantity = Decimal(quantity)
    holding.stock = stock
    return holding


def _make_db(holdings: list, prices: dict[str, str | None]) -> AsyncMock:
    """Build a mock AsyncSession that returns holdings + latest prices.

    ``prices`` maps ticker -> latest close price string (or None to omit).
    ``get_summary`` calls ``db.execute`` twice: first for holdings, then
    for the latest-close lookup.
    """
    holdings_result = MagicMock()
    holdings_result.scalars.return_value.all.return_value = holdings

    price_rows = [(t, Decimal(p)) for t, p in prices.items() if p is not None]
    prices_result = MagicMock()
    prices_result.all.return_value = price_rows

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[holdings_result, prices_result])
    return db


@pytest.mark.asyncio
async def test_summary_no_holdings() -> None:
    db = _make_db([], {})

    summary = await PortfolioService().get_summary(db)

    assert summary.holdings == []
    assert summary.total_value is None


@pytest.mark.asyncio
async def test_summary_single_holding_with_price() -> None:
    db = _make_db(
        [_make_holding(1, "AAPL", "Apple Inc.", "10")],
        {"AAPL": "150.00"},
    )

    summary = await PortfolioService().get_summary(db)

    assert len(summary.holdings) == 1
    item = summary.holdings[0]
    assert item.ticker == "AAPL"
    assert item.current_price == Decimal("150.00")
    assert item.current_value == Decimal("1500.00")
    assert summary.total_value == Decimal("1500.00")


@pytest.mark.asyncio
async def test_summary_holding_without_price() -> None:
    db = _make_db(
        [_make_holding(1, "AAPL", "Apple Inc.", "10")],
        {"AAPL": None},
    )

    summary = await PortfolioService().get_summary(db)

    assert summary.holdings[0].current_value is None
    assert summary.total_value is None


@pytest.mark.asyncio
async def test_summary_mixed_holdings() -> None:
    """Total value counts only holdings that have a cached price."""
    db = _make_db(
        [
            _make_holding(1, "AAPL", "Apple Inc.", "10"),
            _make_holding(2, "TSLA", "Tesla Inc.", "5"),
            _make_holding(3, "MSFT", "Microsoft Corp.", "2"),
        ],
        {"AAPL": "150.00", "TSLA": None, "MSFT": "300.00"},
    )

    summary = await PortfolioService().get_summary(db)

    assert len(summary.holdings) == 3
    assert summary.total_value == Decimal("2100.00")  # 10*150 + 2*300
    assert summary.holdings[1].current_value is None


@pytest.mark.asyncio
async def test_summary_preserves_asset_type() -> None:
    """Each summary item carries the underlying stock's ``asset_type``."""
    db = _make_db(
        [
            _make_holding(1, "AAPL", "Apple Inc.", "10", asset_type="STOCK"),
            _make_holding(2, "BTC-EUR", "Bitcoin EUR", "0.5", asset_type="CRYPTO"),
        ],
        {"AAPL": "150.00", "BTC-EUR": "60000.00"},
    )

    summary = await PortfolioService().get_summary(db)

    by_ticker = {item.ticker: item for item in summary.holdings}
    assert by_ticker["AAPL"].asset_type == "STOCK"
    assert by_ticker["BTC-EUR"].asset_type == "CRYPTO"


@pytest.mark.asyncio
async def test_summary_usd_holding_converted_to_eur() -> None:
    """Holdings in USD are converted to EUR via the FX cache."""
    fx_module._fx_cache.clear()
    fx_module._fx_cache["USD"] = Decimal("1.10")  # 1 EUR = 1.10 USD

    db = _make_db(
        [_make_holding(1, "AAPL", "Apple Inc.", "10", currency="USD")],
        {"AAPL": "110.00"},
    )

    summary = await PortfolioService().get_summary(db)

    expected_eur_price = Decimal("110.00") / Decimal("1.10")
    assert summary.holdings[0].current_price == expected_eur_price
    assert summary.holdings[0].current_value == Decimal("10") * expected_eur_price
    assert summary.total_value == Decimal("10") * expected_eur_price

    fx_module._fx_cache.clear()


# ---------------------------------------------------------------------------
# get_performance_history — transaction-aware (issue #99)
# ---------------------------------------------------------------------------


def _history_db(
    events: list[tuple[int, datetime.datetime, str, str]],
    stocks: dict[int, tuple[str, str]],
    prices: dict[datetime.date, dict[str, str]],
) -> AsyncMock:
    """Build a mock session that returns ordered tuples for the three queries
    portfolio_service.get_performance_history issues:

    1. transaction events:  (stock_id, datetime, type, shares)
    2. stock info:          (id, ticker, currency)
    3. price cache rows:    (ticker, date, close_price)
    """
    event_rows = [(sid, dt, t, Decimal(sh)) for sid, dt, t, sh in events]
    event_result = MagicMock()
    event_result.__iter__ = lambda self: iter(event_rows)

    stock_rows = [(sid, t, c) for sid, (t, c) in stocks.items()]
    stock_result = MagicMock()
    stock_result.__iter__ = lambda self: iter(stock_rows)

    price_rows = [
        (ticker, d, Decimal(p))
        for d, by_ticker in prices.items()
        for ticker, p in by_ticker.items()
    ]
    price_result = MagicMock()
    price_result.__iter__ = lambda self: iter(price_rows)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[event_result, stock_result, price_result])
    return db


@pytest.mark.asyncio
async def test_history_zero_before_buy_then_positive() -> None:
    """A BUY on day 5 → days 1–4 read 0; days 5–9 show the position value."""
    base = datetime.date(2025, 1, 1)
    day = lambda n: base + datetime.timedelta(days=n - 1)  # noqa: E731

    db = _history_db(
        events=[(1, datetime.datetime(2025, 1, 5), "BUY", "10")],
        stocks={1: ("AAPL", "EUR")},
        prices={
            day(n): {"AAPL": "100.00"} for n in range(1, 10)
        },
    )

    history = await PortfolioService().get_performance_history(db)

    by_date = dict(history)
    assert by_date[day(1)] == Decimal("0")
    assert by_date[day(4)] == Decimal("0")
    assert by_date[day(5)] == Decimal("1000.00")
    assert by_date[day(9)] == Decimal("1000.00")


@pytest.mark.asyncio
async def test_history_sell_halves_value() -> None:
    """A BUY of 10 on day 5 + SELL of 5 on day 8 → value halves from day 8."""
    base = datetime.date(2025, 1, 1)
    day = lambda n: base + datetime.timedelta(days=n - 1)  # noqa: E731

    db = _history_db(
        events=[
            (1, datetime.datetime(2025, 1, 5), "BUY", "10"),
            (1, datetime.datetime(2025, 1, 8), "SELL", "5"),
        ],
        stocks={1: ("AAPL", "EUR")},
        prices={day(n): {"AAPL": "100.00"} for n in range(1, 11)},
    )

    history = await PortfolioService().get_performance_history(db)

    by_date = dict(history)
    assert by_date[day(5)] == Decimal("1000.00")
    assert by_date[day(7)] == Decimal("1000.00")
    assert by_date[day(8)] == Decimal("500.00")
    assert by_date[day(10)] == Decimal("500.00")


@pytest.mark.asyncio
async def test_history_forward_fills_missing_price() -> None:
    """A ticker missing a close on one date keeps its last value (no dip).

    Both stocks are bought on day 1 and priced on days 1 and 3, but BTC-EUR
    has no close on day 2.  Without forward-fill, day 2 would drop BTC-EUR and
    collapse the total — the sawtooth.  Day 2 must equal days 1 and 3.
    """
    base = datetime.date(2025, 1, 1)
    day = lambda n: base + datetime.timedelta(days=n - 1)  # noqa: E731

    db = _history_db(
        events=[
            (1, datetime.datetime(2025, 1, 1), "BUY", "10"),
            (2, datetime.datetime(2025, 1, 1), "BUY", "1"),
        ],
        stocks={1: ("AAPL", "EUR"), 2: ("BTC-EUR", "EUR")},
        prices={
            day(1): {"AAPL": "100.00", "BTC-EUR": "50000.00"},
            day(2): {"AAPL": "100.00"},  # BTC-EUR missing
            day(3): {"AAPL": "100.00", "BTC-EUR": "50000.00"},
        },
    )

    history = await PortfolioService().get_performance_history(db)

    by_date = dict(history)
    expected = Decimal("51000.00")  # 10*100 + 1*50000
    assert by_date[day(1)] == expected
    assert by_date[day(2)] == expected
    assert by_date[day(3)] == expected


@pytest.mark.asyncio
async def test_history_returns_empty_when_no_position_events() -> None:
    db = _history_db(events=[], stocks={}, prices={})

    history = await PortfolioService().get_performance_history(db)

    assert history == []
