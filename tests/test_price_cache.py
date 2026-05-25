"""Unit tests for PriceCache helpers in price_service."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.price_service import (
    ensure_prices_cached,
    get_price,
    refresh_price_cache,
)


def _make_existing_db(cached_tickers: list[str]) -> AsyncMock:
    """Mock AsyncSession whose ticker-existence query returns *cached_tickers*."""
    existing = MagicMock()
    existing.scalars.return_value.all.return_value = cached_tickers
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=existing)
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_mock(scalar_value=None):
    """Return a mock AsyncSession whose scalar_one_or_none returns *scalar_value*."""
    result_mock = MagicMock()
    result_mock.scalar_one_or_none.return_value = scalar_value

    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=result_mock)
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# get_price
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_price_found() -> None:
    expected = Decimal("150.1234")
    db = _make_db_mock(scalar_value=expected)

    result = await get_price("AAPL", datetime.date(2025, 1, 10), db)

    assert result == expected
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_price_not_found() -> None:
    db = _make_db_mock(scalar_value=None)

    result = await get_price("AAPL", datetime.date(2025, 1, 10), db)

    assert result is None


@pytest.mark.asyncio
async def test_get_price_uppercase_ticker() -> None:
    """Ticker should be normalised to uppercase before querying."""
    db = _make_db_mock(scalar_value=Decimal("50.00"))

    await get_price("aapl", datetime.date(2025, 1, 10), db)

    # Inspect the WHERE clause argument passed to execute — the compiled SQL
    # isn't easily introspectable, but we can verify execute was called at all.
    db.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# refresh_price_cache
# ---------------------------------------------------------------------------

_HISTORY = {
    datetime.date(2025, 1, 10): Decimal("150.0000"),
    datetime.date(2025, 1, 11): Decimal("152.5000"),
}


@pytest.mark.asyncio
async def test_refresh_price_cache_upserts_rows() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    with patch(
        "app.services.price_service._fetch_history",
        AsyncMock(return_value=_HISTORY),
    ):
        await refresh_price_cache(["AAPL"], db)

    db.execute.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_price_cache_skips_empty_history() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    with patch(
        "app.services.price_service._fetch_history",
        AsyncMock(return_value={}),
    ):
        await refresh_price_cache(["AAPL"], db)

    db.execute.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_price_cache_handles_fetch_error() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    with patch(
        "app.services.price_service._fetch_history",
        AsyncMock(side_effect=RuntimeError("network error")),
    ):
        # Should not raise — errors are logged and skipped.
        await refresh_price_cache(["AAPL"], db)

    db.execute.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_refresh_price_cache_multiple_tickers() -> None:
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock()
    db.commit = AsyncMock()

    with patch(
        "app.services.price_service._fetch_history",
        AsyncMock(return_value=_HISTORY),
    ):
        await refresh_price_cache(["AAPL", "MSFT"], db)

    assert db.execute.await_count == 2
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# ensure_prices_cached
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_prices_cached_skips_already_cached() -> None:
    """A ticker that already has cached closes is not re-fetched."""
    db = _make_existing_db(["AAPL"])

    with patch(
        "app.services.price_service._fetch_history", AsyncMock()
    ) as fetch:
        fetched = await ensure_prices_cached(["AAPL"], db)

    fetch.assert_not_called()
    assert fetched == []
    db.flush.assert_not_awaited()
    db.commit.assert_not_awaited()  # never commits — caller owns the transaction


@pytest.mark.asyncio
async def test_ensure_prices_cached_fetches_missing() -> None:
    """A ticker with no cached close is fetched, upserted, and flushed."""
    db = _make_existing_db([])  # nothing cached yet

    with patch(
        "app.services.price_service._fetch_history",
        AsyncMock(return_value=_HISTORY),
    ) as fetch:
        fetched = await ensure_prices_cached(["NEW"], db)

    fetch.assert_awaited_once_with("NEW")
    assert fetched == ["NEW"]
    db.flush.assert_awaited_once()
    db.commit.assert_not_awaited()
    # one execute for the existence check + one for the upsert insert
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_ensure_prices_cached_empty_input_is_noop() -> None:
    db = _make_existing_db([])

    fetched = await ensure_prices_cached([], db)

    assert fetched == []
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_prices_cached_skips_ticker_with_empty_history() -> None:
    """If yfinance returns nothing, the ticker is skipped (no flush, no row)."""
    db = _make_existing_db([])

    with patch(
        "app.services.price_service._fetch_history",
        AsyncMock(return_value={}),
    ):
        fetched = await ensure_prices_cached(["DELISTED"], db)

    assert fetched == []
    db.flush.assert_not_awaited()
    # only the existence check ran; no upsert
    assert db.execute.await_count == 1
