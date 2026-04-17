"""Unit tests for the FX rate service (app/services/fx_service.py)."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.services.fx_service as fx_module
from app.services.fx_service import (
    load_fx_cache_from_db,
    refresh_fx_rates,
    to_eur,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reset_cache() -> None:
    """Clear the in-memory FX cache between tests."""
    fx_module._fx_cache.clear()


def _make_db(select_rows: list | None = None, scalar: Decimal | None = None) -> AsyncMock:
    """Build a mock AsyncSession.

    ``select_rows`` is used as the return value of ``result.all()`` (for
    ``load_fx_cache_from_db``). ``scalar`` is returned by
    ``scalar_one_or_none`` (for the DB fallback lookup).
    """
    result = MagicMock()
    result.all = MagicMock(return_value=select_rows or [])
    result.scalar_one_or_none = MagicMock(return_value=scalar)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    return db


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
    db = _make_db()
    with patch("app.services.fx_service._fetch_rate_sync", return_value=None):
        await refresh_fx_rates(["EUR"], db)
    assert fx_module._fx_cache.get("EUR") == Decimal("1")


@pytest.mark.asyncio
async def test_refresh_fx_rates_stores_fetched_rate_and_persists() -> None:
    _reset_cache()
    mock_rate = Decimal("1.085432")
    db = _make_db()

    def _fake_fetch(currency: str) -> Decimal | None:
        return mock_rate if currency == "USD" else None

    with patch("app.services.fx_service._fetch_rate_sync", side_effect=_fake_fetch):
        await refresh_fx_rates(["USD"], db)

    assert fx_module._fx_cache.get("USD") == mock_rate
    # An upsert should have been executed, followed by a commit.
    assert db.execute.await_count >= 1
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_fx_rates_falls_back_to_db_on_none_result() -> None:
    _reset_cache()
    db_rate = Decimal("1.07")
    db = _make_db(scalar=db_rate)

    with patch("app.services.fx_service._fetch_rate_sync", return_value=None):
        await refresh_fx_rates(["USD"], db)

    # Cache should contain the DB fallback rate.
    assert fx_module._fx_cache.get("USD") == db_rate


@pytest.mark.asyncio
async def test_refresh_fx_rates_falls_back_to_db_on_exception() -> None:
    _reset_cache()
    db_rate = Decimal("0.86")
    db = _make_db(scalar=db_rate)

    with patch(
        "app.services.fx_service._fetch_rate_sync",
        side_effect=RuntimeError("network error"),
    ):
        # Should not raise
        await refresh_fx_rates(["GBP"], db)

    assert fx_module._fx_cache.get("GBP") == db_rate


@pytest.mark.asyncio
async def test_refresh_fx_rates_no_fallback_when_db_empty() -> None:
    _reset_cache()
    db = _make_db(scalar=None)

    with patch("app.services.fx_service._fetch_rate_sync", return_value=None):
        await refresh_fx_rates(["USD"], db)

    assert "USD" not in fx_module._fx_cache


@pytest.mark.asyncio
async def test_refresh_fx_rates_multiple_currencies() -> None:
    _reset_cache()
    db = _make_db()
    rates = {"USD": Decimal("1.10"), "GBP": Decimal("0.85")}

    def _fake_fetch(currency: str) -> Decimal | None:
        return rates.get(currency)

    with patch("app.services.fx_service._fetch_rate_sync", side_effect=_fake_fetch):
        await refresh_fx_rates(["EUR", "USD", "GBP"], db)

    assert fx_module._fx_cache["EUR"] == Decimal("1")
    assert fx_module._fx_cache["USD"] == Decimal("1.10")
    assert fx_module._fx_cache["GBP"] == Decimal("0.85")


# ---------------------------------------------------------------------------
# load_fx_cache_from_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_fx_cache_from_db_populates_cache() -> None:
    _reset_cache()
    rows = [("USD", Decimal("1.10")), ("GBP", Decimal("0.85"))]
    db = _make_db(select_rows=rows)

    loaded = await load_fx_cache_from_db(db)

    assert loaded == 2
    assert fx_module._fx_cache["EUR"] == Decimal("1")
    assert fx_module._fx_cache["USD"] == Decimal("1.10")
    assert fx_module._fx_cache["GBP"] == Decimal("0.85")


@pytest.mark.asyncio
async def test_load_fx_cache_from_db_empty() -> None:
    _reset_cache()
    db = _make_db(select_rows=[])

    loaded = await load_fx_cache_from_db(db)

    assert loaded == 0
    assert fx_module._fx_cache == {"EUR": Decimal("1")}
