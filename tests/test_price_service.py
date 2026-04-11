"""Unit tests for StockPriceService."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.services.price_service import StockPriceService
from app.services.stock_lookup import StockInfo

_APPLE = StockInfo(
    wkn="865985",
    name="Apple Inc.",
    currency="USD",
    current_price=Decimal("175.00"),
)


@pytest.fixture()
def service() -> StockPriceService:
    return StockPriceService()


@pytest.mark.asyncio
async def test_get_current_price_known_wkn(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=_APPLE)):
        price = await service.get_current_price("865985")
    assert price == Decimal("175.00")


@pytest.mark.asyncio
async def test_get_current_price_unknown_wkn(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=None)):
        price = await service.get_current_price("INVALID")
    assert price is None


@pytest.mark.asyncio
async def test_get_company_name_known_wkn(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=_APPLE)):
        name = await service.get_company_name("865985")
    assert name == "Apple Inc."


@pytest.mark.asyncio
async def test_get_company_name_unknown_wkn(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=None)):
        name = await service.get_company_name("INVALID")
    assert name is None


@pytest.mark.asyncio
async def test_validate_wkn_valid(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=_APPLE)):
        assert await service.validate_wkn("865985") is True


@pytest.mark.asyncio
async def test_validate_wkn_invalid(service: StockPriceService) -> None:
    with patch("app.services.price_service.fetch_stock_info", AsyncMock(return_value=None)):
        assert await service.validate_wkn("INVALID") is False
