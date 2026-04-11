"""Unit tests for the FX rate service (app/services/fx_service.py)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest

import app.services.fx_service as fx_module
from app.services.fx_service import refresh_fx_rates, to_eur

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_cache() -> None:
    """Clear the in-memory FX cache between tests."""
    fx_module._fx_cache.clear()


# ---------------------------------------------------------------------------
# to_eur
# ---------------------------------------------------------------------------


def test_to_eur_already_eur() -> None:
    _reset_cache()
    result = to_eur(Decimal("100"), "EUR")
    assert result == Decimal("100")


def test_to_eur_usd_with_cached_rate() -> None:
    _reset_cache()
    # EURUSD=X = 1.10 means 1 EUR = 1.10 USD
    fx_module._fx_cache["USD"] = Decimal("1.10")
    result = to_eur(Decimal("110"), "USD")
    assert result == Decimal("110") / Decimal("1.10")


def test_to_eur_gbp_with_cached_rate() -> None:
    _reset_cache()
    # EURGBP=X = 0.85 means 1 EUR = 0.85 GBP
    fx_module._fx_cache["GBP"] = Decimal("0.85")
    result = to_eur(Decimal("85"), "GBP")
    assert result == Decimal("85") / Decimal("0.85")


def test_to_eur_no_cached_rate_returns_unchanged() -> None:
    _reset_cache()
    result = to_eur(Decimal("200"), "USD")
    assert result == Decimal("200")


def test_to_eur_currency_case_insensitive() -> None:
    _reset_cache()
    fx_module._fx_cache["USD"] = Decimal("1.10")
    result_upper = to_eur(Decimal("110"), "USD")
    result_lower = to_eur(Decimal("110"), "usd")
    assert result_upper == result_lower


def test_to_eur_eur_stock_conversion_factor_is_one() -> None:
    _reset_cache()
    fx_module._fx_cache["EUR"] = Decimal("1")
    result = to_eur(Decimal("50"), "EUR")
    assert result == Decimal("50")


# ---------------------------------------------------------------------------
# refresh_fx_rates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_fx_rates_sets_eur_to_one() -> None:
    _reset_cache()
    with patch("app.services.fx_service._fetch_rate_sync", return_value=None):
        await refresh_fx_rates(["EUR"])
    assert fx_module._fx_cache.get("EUR") == Decimal("1")


@pytest.mark.asyncio
async def test_refresh_fx_rates_stores_fetched_rate() -> None:
    _reset_cache()
    mock_rate = Decimal("1.085432")

    def _fake_fetch(currency: str) -> Decimal | None:
        return mock_rate if currency == "USD" else None

    with patch("app.services.fx_service._fetch_rate_sync", side_effect=_fake_fetch):
        await refresh_fx_rates(["USD"])

    assert fx_module._fx_cache.get("USD") == mock_rate


@pytest.mark.asyncio
async def test_refresh_fx_rates_skips_on_none_result() -> None:
    _reset_cache()
    with patch("app.services.fx_service._fetch_rate_sync", return_value=None):
        await refresh_fx_rates(["USD"])
    assert "USD" not in fx_module._fx_cache


@pytest.mark.asyncio
async def test_refresh_fx_rates_handles_exception_gracefully() -> None:
    _reset_cache()
    with patch(
        "app.services.fx_service._fetch_rate_sync",
        side_effect=RuntimeError("network error"),
    ):
        # Should not raise
        await refresh_fx_rates(["USD"])
    assert "USD" not in fx_module._fx_cache


@pytest.mark.asyncio
async def test_refresh_fx_rates_multiple_currencies() -> None:
    _reset_cache()
    rates = {"USD": Decimal("1.10"), "GBP": Decimal("0.85")}

    def _fake_fetch(currency: str) -> Decimal | None:
        return rates.get(currency)

    with patch("app.services.fx_service._fetch_rate_sync", side_effect=_fake_fetch):
        await refresh_fx_rates(["EUR", "USD", "GBP"])

    assert fx_module._fx_cache["EUR"] == Decimal("1")
    assert fx_module._fx_cache["USD"] == Decimal("1.10")
    assert fx_module._fx_cache["GBP"] == Decimal("0.85")
