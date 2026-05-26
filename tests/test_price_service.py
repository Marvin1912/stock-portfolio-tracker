"""Unit tests for StockPriceService."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.services.price_service import StockPriceService, _fetch_history_sync
from app.services.stock_lookup import StockInfo

_APPLE = StockInfo(
    ticker="AAPL",
    name="Apple Inc.",
    currency="USD",
    current_price=Decimal("175.00"),
)


@pytest.fixture()
def service() -> StockPriceService:
    return StockPriceService()


@pytest.mark.asyncio
async def test_get_current_price_known_ticker(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=_APPLE)):
        price = await service.get_current_price("AAPL")
    assert price == Decimal("175.00")


@pytest.mark.asyncio
async def test_get_current_price_unknown_ticker(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=None)):
        price = await service.get_current_price("INVALID")
    assert price is None


@pytest.mark.asyncio
async def test_get_company_name_known_ticker(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=_APPLE)):
        name = await service.get_company_name("AAPL")
    assert name == "Apple Inc."


@pytest.mark.asyncio
async def test_get_company_name_unknown_ticker(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=None)):
        name = await service.get_company_name("INVALID")
    assert name is None


@pytest.mark.asyncio
async def test_validate_ticker_valid(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=_APPLE)):
        assert await service.validate_ticker("AAPL") is True


@pytest.mark.asyncio
async def test_validate_ticker_invalid(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=None)):
        assert await service.validate_ticker("INVALID") is False


def test_fetch_history_skips_nan_close() -> None:
    """A NaN close (e.g. a partial-holiday bar) must not be stored."""
    index = pd.to_datetime(["2026-05-22", "2026-05-25"])
    hist = pd.DataFrame({"Close": [102.37, float("nan")]}, index=index)
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = hist

    with patch("yfinance.Ticker", return_value=fake_ticker):
        result = _fetch_history_sync("EUNL.DE")

    assert result == {datetime.date(2026, 5, 22): Decimal("102.37")}
    assert datetime.date(2026, 5, 25) not in result
