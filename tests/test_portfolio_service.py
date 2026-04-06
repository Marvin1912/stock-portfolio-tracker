"""Unit tests for PortfolioService — uses mocked DB session."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.portfolio_service import PortfolioService


def _make_holding(
    id: int,
    ticker: str,
    name: str,
    quantity: str,
    current_price: str | None,
) -> MagicMock:
    stock = MagicMock()
    stock.ticker = ticker
    stock.name = name
    stock.current_price = Decimal(current_price) if current_price else None

    holding = MagicMock()
    holding.id = id
    holding.quantity = Decimal(quantity)
    holding.stock = stock
    return holding


@pytest.mark.asyncio
async def test_summary_no_holdings() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)

    summary = await PortfolioService().get_summary(db)

    assert summary.holdings == []
    assert summary.total_value is None


@pytest.mark.asyncio
async def test_summary_single_holding_with_price() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [
        _make_holding(1, "AAPL", "Apple Inc.", "10", "150.00")
    ]
    db.execute = AsyncMock(return_value=result)

    summary = await PortfolioService().get_summary(db)

    assert len(summary.holdings) == 1
    item = summary.holdings[0]
    assert item.ticker == "AAPL"
    assert item.current_price == Decimal("150.00")
    assert item.current_value == Decimal("1500.00")
    assert summary.total_value == Decimal("1500.00")


@pytest.mark.asyncio
async def test_summary_holding_without_price() -> None:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [
        _make_holding(1, "AAPL", "Apple Inc.", "10", None)
    ]
    db.execute = AsyncMock(return_value=result)

    summary = await PortfolioService().get_summary(db)

    assert summary.holdings[0].current_value is None
    assert summary.total_value is None


@pytest.mark.asyncio
async def test_summary_mixed_holdings() -> None:
    """Total value counts only holdings that have a price."""
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = [
        _make_holding(1, "AAPL", "Apple Inc.", "10", "150.00"),
        _make_holding(2, "TSLA", "Tesla Inc.", "5", None),
        _make_holding(3, "MSFT", "Microsoft Corp.", "2", "300.00"),
    ]
    db.execute = AsyncMock(return_value=result)

    summary = await PortfolioService().get_summary(db)

    assert len(summary.holdings) == 3
    assert summary.total_value == Decimal("2100.00")  # 10*150 + 2*300
    assert summary.holdings[1].current_value is None
