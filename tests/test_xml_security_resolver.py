"""Tests for the XML security resolver — verifies the 4-step fallback chain."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

from app.services.portfolio_performance_importer import SecurityInfo
from app.services.stock_lookup import StockInfo
from app.services.xml_security_resolver import (
    resolve_securities,
    resolve_security,
)


def _info(
    name: str = "Stock",
    currency: str = "EUR",
    quote_type: str | None = "EQUITY",
) -> StockInfo:
    return StockInfo(
        ticker="T",
        name=name,
        currency=currency,
        current_price=Decimal("1"),
        quote_type=quote_type,
    )


def _security(**overrides):  # type: ignore[no-untyped-def]
    base = dict(uuid="u", name="X", isin="DE0001", ticker="X", currency="EUR")
    base.update(overrides)
    return SecurityInfo(**base)


@pytest.mark.asyncio
async def test_resolves_via_raw_xml_ticker_when_yahoo_knows_it() -> None:
    sec = _security(ticker="SAP.DE")
    with patch(
        "app.services.xml_security_resolver.fetch_stock_info",
        new=AsyncMock(side_effect=[_info("SAP SE")]),
    ) as mock_fetch:
        result = await resolve_security(sec)

    assert result.status == "valid"
    assert result.suggestion_source == "xml"
    assert result.resolved_ticker == "SAP.DE"
    assert result.asset_type == "STOCK"
    assert mock_fetch.await_count == 1


@pytest.mark.asyncio
async def test_falls_back_to_openfigi_when_raw_ticker_unknown() -> None:
    sec = _security(ticker="SYK.TG", isin="US58933Y1055")
    with (
        patch(
            "app.services.xml_security_resolver.fetch_stock_info",
            new=AsyncMock(side_effect=[None, _info("Stryker Corp")]),
        ),
        patch(
            "app.services.xml_security_resolver.resolve_isin",
            new=AsyncMock(return_value="SYK"),
        ) as mock_isin,
    ):
        result = await resolve_security(sec, openfigi_api_key="k")

    assert result.status == "valid"
    assert result.suggestion_source == "openfigi"
    assert result.resolved_ticker == "SYK"
    mock_isin.assert_awaited_once_with("US58933Y1055", api_key="k")


@pytest.mark.asyncio
async def test_crypto_pair_heuristic_for_short_symbol_without_isin() -> None:
    sec = _security(ticker="DOGE", isin=None)
    with patch(
        "app.services.xml_security_resolver.fetch_stock_info",
        new=AsyncMock(
            side_effect=[
                None,
                _info("Dogecoin EUR", currency="EUR", quote_type="CRYPTOCURRENCY"),
            ]
        ),
    ):
        result = await resolve_security(sec)

    assert result.status == "valid"
    assert result.suggestion_source == "crypto_pair"
    assert result.resolved_ticker == "DOGE-EUR"
    assert result.asset_type == "CRYPTO"


@pytest.mark.asyncio
async def test_crypto_tries_usd_when_eur_pair_unknown() -> None:
    sec = _security(ticker="IOTA", isin=None)
    with patch(
        "app.services.xml_security_resolver.fetch_stock_info",
        new=AsyncMock(
            side_effect=[
                None,
                None,
                _info("IOTA USD", currency="USD", quote_type="CRYPTOCURRENCY"),
            ]
        ),
    ):
        result = await resolve_security(sec)

    assert result.status == "valid"
    assert result.resolved_ticker == "IOTA-USD"
    assert result.asset_type == "CRYPTO"


@pytest.mark.asyncio
async def test_raw_ticker_marked_crypto_when_yahoo_says_cryptocurrency() -> None:
    """BTC-EUR resolves via Step 1 but Yahoo's quoteType drives the classification."""
    sec = _security(ticker="BTC-EUR", isin=None)
    with patch(
        "app.services.xml_security_resolver.fetch_stock_info",
        new=AsyncMock(
            side_effect=[_info("Bitcoin EUR", quote_type="CRYPTOCURRENCY")]
        ),
    ):
        result = await resolve_security(sec)

    assert result.status == "valid"
    assert result.suggestion_source == "xml"
    assert result.resolved_ticker == "BTC-EUR"
    assert result.asset_type == "CRYPTO"


@pytest.mark.asyncio
async def test_openfigi_resolved_etp_stays_stock() -> None:
    """A crypto ETP resolved via OpenFIGI has quoteType=EQUITY → STOCK."""
    sec = _security(ticker=None, isin="DE000A27Z304")
    with (
        patch(
            "app.services.xml_security_resolver.fetch_stock_info",
            new=AsyncMock(
                side_effect=[_info("BTCetc Physical Bitcoin", quote_type="EQUITY")]
            ),
        ),
        patch(
            "app.services.xml_security_resolver.resolve_isin",
            new=AsyncMock(return_value="BTCE.DE"),
        ),
    ):
        result = await resolve_security(sec, openfigi_api_key="k")

    assert result.status == "valid"
    assert result.suggestion_source == "openfigi"
    assert result.resolved_ticker == "BTCE.DE"
    assert result.asset_type == "STOCK"


@pytest.mark.asyncio
async def test_marks_needs_attention_when_everything_fails() -> None:
    sec = _security(ticker="ZZZ.UNKNOWN", isin="DE0000000000")
    with (
        patch(
            "app.services.xml_security_resolver.fetch_stock_info",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "app.services.xml_security_resolver.resolve_isin",
            new=AsyncMock(return_value=None),
        ),
    ):
        result = await resolve_security(sec)

    assert result.status == "needs_attention"
    assert result.resolved_ticker is None
    assert result.suggestion_source == "manual"


@pytest.mark.asyncio
async def test_resolve_securities_runs_in_parallel_and_keys_by_uuid() -> None:
    secs = [_security(uuid="a", ticker="AAA"), _security(uuid="b", ticker="BBB")]
    with patch(
        "app.services.xml_security_resolver.fetch_stock_info",
        new=AsyncMock(side_effect=[_info("A"), _info("B")]),
    ):
        out = await resolve_securities(secs)

    assert set(out.keys()) == {"a", "b"}
    assert out["a"].status == "valid"
    assert out["b"].status == "valid"
