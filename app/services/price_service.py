"""Class-based stock price service wrapping yfinance."""

from __future__ import annotations

import asyncio
import datetime
import logging
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

    await db.commit()
    logger.info("Price cache refreshed for %d ticker(s).", len(tickers))


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
