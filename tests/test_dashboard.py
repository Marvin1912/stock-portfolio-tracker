"""Tests for GET /api/v1/portfolio/value and /api/v1/portfolio/bitcoin."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient

from app.schemas.holdings import HoldingSummaryItem, PortfolioSummary
from app.services import chart_cache


def _btc_holding() -> HoldingSummaryItem:
    return HoldingSummaryItem(
        id=1,
        ticker="BTC-EUR",
        name="Bitcoin",
        asset_type="CRYPTO",
        quantity=Decimal("0.5"),
        current_price=Decimal("45000.00"),
        current_value=Decimal("22500.00"),
    )


def _stock_holding() -> HoldingSummaryItem:
    return HoldingSummaryItem(
        id=2,
        ticker="AAPL",
        name="Apple Inc.",
        asset_type="STOCK",
        quantity=Decimal("10"),
        current_price=Decimal("180.00"),
        current_value=Decimal("1800.00"),
    )


def _seed_cache(holdings: list[HoldingSummaryItem], total_value: Decimal | None) -> None:
    chart_cache.set("summary", PortfolioSummary(holdings=holdings, total_value=total_value))


# ---------------------------------------------------------------------------
# /api/v1/portfolio/value
# ---------------------------------------------------------------------------


async def test_portfolio_value_no_holdings(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    _seed_cache([], None)

    response = await client.get("/api/v1/portfolio/value")
    assert response.status_code == 200
    data = response.json()
    assert data["total_value"] is None
    assert data["currency"] == "EUR"
    assert "as_of" in data


async def test_portfolio_value_with_holdings(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    _seed_cache([_btc_holding(), _stock_holding()], Decimal("24300.00"))

    response = await client.get("/api/v1/portfolio/value")
    assert response.status_code == 200
    data = response.json()
    assert Decimal(data["total_value"]) == Decimal("24300.00")
    assert data["currency"] == "EUR"


# ---------------------------------------------------------------------------
# /api/v1/portfolio/bitcoin
# ---------------------------------------------------------------------------


async def test_bitcoin_not_found(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    _seed_cache([_stock_holding()], Decimal("1800.00"))

    response = await client.get("/api/v1/portfolio/bitcoin")
    assert response.status_code == 404
    assert "Bitcoin" in response.json()["detail"]


async def test_bitcoin_returns_correct_fields(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    _seed_cache([_btc_holding(), _stock_holding()], Decimal("24300.00"))

    response = await client.get("/api/v1/portfolio/bitcoin")
    assert response.status_code == 200
    data = response.json()
    assert data["ticker"] == "BTC-EUR"
    assert data["name"] == "Bitcoin"
    assert Decimal(data["quantity"]) == Decimal("0.5")
    assert Decimal(data["current_price"]) == Decimal("45000.00")
    assert Decimal(data["current_value"]) == Decimal("22500.00")


async def test_bitcoin_percentage_of_portfolio(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    _seed_cache([_btc_holding(), _stock_holding()], Decimal("24300.00"))

    response = await client.get("/api/v1/portfolio/bitcoin")
    data = response.json()
    expected = (Decimal("22500.00") / Decimal("24300.00") * 100).quantize(Decimal("0.01"))
    assert Decimal(data["percentage_of_portfolio"]) == expected


async def test_bitcoin_no_price_no_percentage(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    unpriced_btc = HoldingSummaryItem(
        id=1,
        ticker="BTC-EUR",
        name="Bitcoin",
        asset_type="CRYPTO",
        quantity=Decimal("0.5"),
        current_price=None,
        current_value=None,
    )
    _seed_cache([unpriced_btc], None)

    response = await client.get("/api/v1/portfolio/bitcoin")
    assert response.status_code == 200
    data = response.json()
    assert data["current_price"] is None
    assert data["current_value"] is None
    assert data["percentage_of_portfolio"] is None
