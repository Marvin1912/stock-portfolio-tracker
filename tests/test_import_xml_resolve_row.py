"""Router tests for /import/xml/resolve-row — focus on the asset_type toggle.

Verifies that switching a row from STOCK to CRYPTO re-derives the actual
crypto pair (``BTC-EUR``) instead of just relabelling the original stock
ticker — the Grayscale-Bitcoin-ETF vs. Bitcoin scenario.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.services import import_cache
from app.services.portfolio_performance_importer import ParseResult, SecurityInfo
from app.services.stock_lookup import StockInfo
from app.services.xml_security_resolver import ResolvedSecurity

pytestmark = pytest.mark.asyncio


def _settings() -> Settings:
    return Settings(
        app_env="development",
        app_debug=False,
        secret_key="test-secret-key-that-is-long-enough-32chars",
        database_url="postgresql+asyncpg://unused/unused",
    )


def _client() -> AsyncClient:
    app = create_app(settings=_settings())
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _store_btc_as_stock() -> str:
    """Seed import_cache with the row that auto-resolved BTC → Grayscale ETF."""
    sec = SecurityInfo(uuid="btc-uuid", name="Grayscale Bitcoin Mini Trust ETF",
                       ticker="BTC", currency="USD")
    resolved = ResolvedSecurity(
        uuid="btc-uuid",
        original_ticker="BTC",
        original_name="Grayscale Bitcoin Mini Trust ETF",
        isin=None,
        status="valid",
        resolved_ticker="BTC",
        asset_type="STOCK",
        suggestion_source="xml",
        yahoo_name="Grayscale Bitcoin Mini Trust ETF",
        currency="USD",
    )
    entry = import_cache.ImportPreviewEntry(
        parse_result=ParseResult(
            version=None,
            base_currency="EUR",
            transactions=[],
            securities={"btc-uuid": sec},
        ),
        resolutions={"btc-uuid": resolved},
        filename="test.xml",
    )
    return import_cache.store(entry)


async def test_toggle_btc_stock_to_crypto_finds_btc_eur_pair() -> None:
    token = _store_btc_as_stock()

    btc_eur = StockInfo(
        ticker="BTC-EUR",
        name="Bitcoin EUR",
        currency="EUR",
        current_price=Decimal("60000"),
        quote_type="CRYPTOCURRENCY",
    )

    async with _client() as client:
        with patch(
            "app.services.xml_security_resolver.fetch_stock_info",
            new=AsyncMock(return_value=btc_eur),
        ):
            response = await client.post(
                "/import/xml/resolve-row",
                data={
                    "token": token,
                    "uuid": "btc-uuid",
                    "ticker": "BTC",
                    "asset_type": "CRYPTO",
                },
            )

    assert response.status_code == 200
    # The cached resolution should now point at the real crypto pair.
    entry = import_cache.get(token)
    assert entry is not None
    updated = entry.resolutions["btc-uuid"]
    assert updated.status == "valid"
    assert updated.resolved_ticker == "BTC-EUR"
    assert updated.asset_type == "CRYPTO"
    assert updated.suggestion_source == "crypto_pair"
    assert "BTC-EUR" in response.text
    import_cache.delete(token)


async def test_toggle_to_crypto_drops_to_needs_attention_when_no_pair_found() -> None:
    """An ETN symbol like BTCE has no matching ``-EUR``/``-USD`` pair on Yahoo —
    the row must drop to needs_attention with the stem prefilled, not stay
    valid with a mislabelled ticker."""
    sec = SecurityInfo(uuid="btce-uuid", name="BTCetc Physical Bitcoin",
                       ticker="BTCE.DE", currency="EUR")
    resolved = ResolvedSecurity(
        uuid="btce-uuid",
        original_ticker="BTCE.DE",
        original_name="BTCetc Physical Bitcoin",
        isin=None,
        status="valid",
        resolved_ticker="BTCE.DE",
        asset_type="STOCK",
        suggestion_source="xml",
        yahoo_name="BTCetc Physical Bitcoin",
        currency="EUR",
    )
    entry = import_cache.ImportPreviewEntry(
        parse_result=ParseResult(
            version=None,
            base_currency="EUR",
            transactions=[],
            securities={"btce-uuid": sec},
        ),
        resolutions={"btce-uuid": resolved},
        filename="test.xml",
    )
    token = import_cache.store(entry)

    async with _client() as client:
        with patch(
            "app.services.xml_security_resolver.fetch_stock_info",
            new=AsyncMock(return_value=None),
        ):
            response = await client.post(
                "/import/xml/resolve-row",
                data={
                    "token": token,
                    "uuid": "btce-uuid",
                    "ticker": "BTCE.DE",
                    "asset_type": "CRYPTO",
                },
            )

    assert response.status_code == 200
    entry = import_cache.get(token)
    assert entry is not None
    updated = entry.resolutions["btce-uuid"]
    assert updated.status == "needs_attention"
    assert updated.asset_type == "CRYPTO"
    # Stem prefilled so the user only has to fix the symbol, not retype the row.
    assert updated.resolved_ticker == "BTCE"
    import_cache.delete(token)
