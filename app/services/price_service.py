"""Class-based stock price service wrapping yfinance."""

from __future__ import annotations

import asyncio
import datetime
import logging
from collections.abc import Iterable
from decimal import Decimal
from functools import partial

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price_cache import PriceCache
from app.services.stock_lookup import fetch_stock_info

logger = logging.getLogger(__name__)


def _fetch_history_sync(ticker: str) -> dict[datetime.date, Decimal]:
    """Fetch 1Y of daily closing prices via yfinance (blocking).

    Returns a mapping of {date: close_price}.
    """
    import yfinance as yf  # type: ignore[import-untyped]

    hist = yf.Ticker(ticker.upper()).history(period="1y")
    if hist.empty:
        return {}
    result: dict[datetime.date, Decimal] = {}
    for ts, row in hist["Close"].items():
        date = ts.date() if hasattr(ts, "date") else ts
        result[date] = Decimal(str(round(float(row), 4)))
    return result


async def _fetch_history(ticker: str) -> dict[datetime.date, Decimal]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_fetch_history_sync, ticker))


async def _upsert_history(
    ticker: str, history: dict[datetime.date, Decimal], db: AsyncSession
) -> None:
    """Upsert a ticker's ``{date: close}`` history into PriceCache (no commit)."""
    rows = [
        {"ticker": ticker.upper(), "date": date, "close_price": price}
        for date, price in history.items()
    ]
    stmt = (
        insert(PriceCache)
        .values(rows)
        .on_conflict_do_update(
            constraint="uq_price_cache_ticker_date",
            set_={"close_price": insert(PriceCache).excluded.close_price},
        )
    )
    await db.execute(stmt)


async def refresh_price_cache(tickers: list[str], db: AsyncSession) -> None:
    """Fetch 1Y of daily closes for each ticker and upsert into PriceCache.

    Intended to be called on startup and once per day via the scheduler.
    """
    for ticker in tickers:
        try:
            history = await _fetch_history(ticker)
        except Exception:
            logger.exception("Failed to fetch history for %s", ticker)
            continue

        if not history:
            logger.warning("No history returned for %s", ticker)
            continue

        await _upsert_history(ticker, history, db)

    await db.commit()
    logger.info("Price cache refreshed for %d ticker(s).", len(tickers))


async def ensure_prices_cached(tickers: Iterable[str], db: AsyncSession) -> list[str]:
    """Fetch and cache 1Y of closes for any *tickers* not yet in PriceCache.

    Called right after an import so a freshly added ticker contributes to the
    portfolio total immediately, instead of showing no value until the daily
    scheduler runs.  Tickers that already have a cached close are skipped, so
    re-imports stay cheap and only genuinely-new securities hit the network.

    Unlike :func:`refresh_price_cache`, this does *not* commit — the caller's
    request-scoped session owns the transaction boundary.  Returns the tickers
    that were freshly fetched.
    """
    wanted = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    if not wanted:
        return []

    existing = await db.execute(
        select(PriceCache.ticker).where(PriceCache.ticker.in_(wanted)).distinct()
    )
    already_cached = set(existing.scalars().all())
    missing = [t for t in wanted if t not in already_cached]

    fetched: list[str] = []
    for ticker in missing:
        try:
            history = await _fetch_history(ticker)
        except Exception:
            logger.exception("Failed to fetch history for %s", ticker)
            continue

        if not history:
            logger.warning("No history returned for %s", ticker)
            continue

        await _upsert_history(ticker, history, db)
        fetched.append(ticker)

    if fetched:
        await db.flush()
        logger.info("Cached prices for freshly imported ticker(s): %s", fetched)
    return fetched


async def get_price(ticker: str, date: datetime.date, db: AsyncSession) -> Decimal | None:
    """Return the cached closing price for *ticker* on *date*, or None."""
    result = await db.execute(
        select(PriceCache.close_price).where(
            PriceCache.ticker == ticker.upper(),
            PriceCache.date == date,
        )
    )
    row = result.scalar_one_or_none()
    return row


async def get_latest_close(ticker: str, db: AsyncSession) -> Decimal | None:
    """Return the most recently cached close price for *ticker*, or None."""
    result = await db.execute(
        select(PriceCache.close_price)
        .where(PriceCache.ticker == ticker.upper())
        .order_by(PriceCache.date.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


class StockPriceService:
    """Provides price and metadata lookups for stock tickers via yfinance."""

    async def get_current_price(self, ticker: str) -> Decimal | None:
        """Return the current price for *ticker*, or None if unavailable."""
        info = await fetch_stock_info(ticker)
        if info is None:
            return None
        return info.current_price

    async def get_company_name(self, ticker: str) -> str | None:
        """Return the company name for *ticker*, or None if the ticker is invalid."""
        info = await fetch_stock_info(ticker)
        if info is None:
            return None
        return info.name

    async def validate_ticker(self, ticker: str) -> bool:
        """Return True if *ticker* resolves to a known, non-delisted security."""
        info = await fetch_stock_info(ticker)
        return info is not None
