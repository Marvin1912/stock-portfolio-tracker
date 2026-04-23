"""Tests for the HTMX crypto add flow — mock-based, no DB required."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.database import get_async_session
from app.main import create_app
from app.models.stock import ASSET_TYPE_CRYPTO, Stock
from app.services.stock_lookup import StockInfo

pytestmark = pytest.mark.asyncio


_BTC_EUR = StockInfo(
    ticker="BTC-EUR",
    name="Bitcoin EUR",
    currency="EUR",
    current_price=Decimal("60000.00"),
)


def _settings() -> Settings:
    return Settings(
        app_env="development",
        app_debug=False,
        secret_key="test-secret-key-that-is-long-enough-32chars",
        database_url="postgresql+asyncpg://unused/unused",
    )


def _mock_db(existing_stock: Stock | None = None) -> MagicMock:
    """Build a mocked AsyncSession for the add-crypto code path."""
    db = MagicMock()

    stock_result = MagicMock()
    stock_result.scalar_one_or_none.return_value = existing_stock

    db.execute = AsyncMock(return_value=stock_result)
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda obj: setattr(obj, "id", 42))
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


async def _client(db: MagicMock) -> AsyncClient:
    """Build an AsyncClient that serves the app with a mocked DB dependency."""
    app = create_app(settings=_settings())

    async def _override() -> MagicMock:
        return db

    app.dependency_overrides[get_async_session] = _override
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_add_crypto_creates_stock_with_asset_type_crypto() -> None:
    db = _mock_db(existing_stock=None)

    created: list[Stock] = []

    def _capture_add(obj: object) -> None:
        if isinstance(obj, Stock):
            obj.id = 7
            created.append(obj)

    db.add.side_effect = _capture_add

    async with await _client(db) as client:
        with patch(
            "app.routers.htmx.fetch_stock_info", AsyncMock(return_value=_BTC_EUR)
        ):
            response = await client.post(
                "/htmx/crypto-holdings",
                data={"symbol": "BTC", "quote": "EUR", "quantity": "0.05"},
            )

    assert response.status_code == 200
    assert "BTC-EUR" in response.text
    assert "Crypto" in response.text
    assert len(created) == 1
    assert created[0].ticker == "BTC-EUR"
    assert created[0].asset_type == ASSET_TYPE_CRYPTO
    assert created[0].currency == "EUR"


async def test_add_crypto_reuses_existing_stock_without_lookup() -> None:
    existing = Stock(
        ticker="BTC-EUR",
        name="Bitcoin EUR",
        currency="EUR",
        asset_type=ASSET_TYPE_CRYPTO,
        current_price=Decimal("50000"),
    )
    existing.id = 3
    db = _mock_db(existing_stock=existing)

    lookup = AsyncMock(return_value=None)
    async with await _client(db) as client:
        with patch("app.routers.htmx.fetch_stock_info", lookup):
            response = await client.post(
                "/htmx/crypto-holdings",
                data={"symbol": "BTC", "quote": "EUR", "quantity": "0.2"},
            )

    assert response.status_code == 200
    lookup.assert_not_awaited()


async def test_add_crypto_normalises_symbol_and_quote() -> None:
    db = _mock_db(existing_stock=None)

    async with await _client(db) as client:
        lookup = AsyncMock(return_value=_BTC_EUR)
        with patch("app.routers.htmx.fetch_stock_info", lookup):
            response = await client.post(
                "/htmx/crypto-holdings",
                data={"symbol": "  btc  ", "quote": "eur", "quantity": "0.1"},
            )

    assert response.status_code == 200
    lookup.assert_awaited_once_with("BTC-EUR")


async def test_add_crypto_rejects_unknown_quote() -> None:
    db = _mock_db()
    async with await _client(db) as client:
        response = await client.post(
            "/htmx/crypto-holdings",
            data={"symbol": "BTC", "quote": "JPY", "quantity": "0.05"},
        )

    assert response.status_code == 200
    assert "Quote currency must be one of" in response.text
    assert 'id="add-crypto-form"' in response.text
    db.add.assert_not_called()


async def test_add_crypto_rejects_empty_symbol() -> None:
    db = _mock_db()
    async with await _client(db) as client:
        response = await client.post(
            "/htmx/crypto-holdings",
            data={"symbol": "   ", "quote": "EUR", "quantity": "0.05"},
        )

    assert response.status_code == 200
    assert "crypto symbol" in response.text.lower()
    db.add.assert_not_called()


@pytest.mark.parametrize("quantity", ["0", "-1", "abc"])
async def test_add_crypto_rejects_invalid_quantity(quantity: str) -> None:
    db = _mock_db()
    async with await _client(db) as client:
        response = await client.post(
            "/htmx/crypto-holdings",
            data={"symbol": "BTC", "quote": "EUR", "quantity": quantity},
        )

    assert response.status_code == 200
    assert "Quantity must be a positive number" in response.text
    db.add.assert_not_called()


async def test_add_crypto_ticker_not_found_shows_error() -> None:
    db = _mock_db(existing_stock=None)
    async with await _client(db) as client:
        with patch(
            "app.routers.htmx.fetch_stock_info", AsyncMock(return_value=None)
        ):
            response = await client.post(
                "/htmx/crypto-holdings",
                data={"symbol": "ZZZ", "quote": "EUR", "quantity": "0.1"},
            )

    assert response.status_code == 200
    assert "not found" in response.text.lower()
    db.add.assert_not_called()


async def test_add_crypto_form_renders() -> None:
    db = _mock_db()
    async with await _client(db) as client:
        response = await client.get("/htmx/holdings/add-crypto-form")

    assert response.status_code == 200
    assert 'id="add-crypto-form"' in response.text
    assert 'value="EUR"' in response.text
    assert 'value="USD"' in response.text


async def test_validate_crypto_empty_symbol_returns_empty() -> None:
    db = _mock_db()
    async with await _client(db) as client:
        response = await client.get(
            "/htmx/validate-crypto", params={"symbol": "", "quote": "EUR"}
        )
    assert response.status_code == 200
    assert response.text == ""


async def test_validate_crypto_invalid_quote_returns_invalid_hint() -> None:
    db = _mock_db()
    async with await _client(db) as client:
        response = await client.get(
            "/htmx/validate-crypto", params={"symbol": "BTC", "quote": "JPY"}
        )

    assert response.status_code == 200
    assert "Ticker not found" in response.text
    assert "ticker-hint negative" in response.text


async def test_validate_crypto_hits_db_first() -> None:
    existing = Stock(
        ticker="BTC-EUR",
        name="Bitcoin EUR",
        currency="EUR",
        asset_type=ASSET_TYPE_CRYPTO,
        current_price=Decimal("50000"),
    )
    existing.id = 1
    db = _mock_db(existing_stock=existing)
    lookup = AsyncMock(return_value=None)

    async with await _client(db) as client:
        with patch("app.routers.htmx.fetch_stock_info", lookup):
            response = await client.get(
                "/htmx/validate-crypto", params={"symbol": "BTC", "quote": "EUR"}
            )

    assert response.status_code == 200
    assert "Bitcoin EUR" in response.text
    lookup.assert_not_awaited()


async def test_validate_crypto_falls_back_to_yfinance() -> None:
    db = _mock_db(existing_stock=None)
    async with await _client(db) as client:
        with patch(
            "app.routers.htmx.fetch_stock_info", AsyncMock(return_value=_BTC_EUR)
        ):
            response = await client.get(
                "/htmx/validate-crypto", params={"symbol": "BTC", "quote": "EUR"}
            )

    assert response.status_code == 200
    assert "Bitcoin EUR" in response.text


async def test_validate_crypto_unknown_symbol_returns_invalid() -> None:
    db = _mock_db(existing_stock=None)
    async with await _client(db) as client:
        with patch(
            "app.routers.htmx.fetch_stock_info", AsyncMock(return_value=None)
        ):
            response = await client.get(
                "/htmx/validate-crypto",
                params={"symbol": "NOTAREALCOIN", "quote": "EUR"},
            )

    assert response.status_code == 200
    assert "Ticker not found" in response.text
