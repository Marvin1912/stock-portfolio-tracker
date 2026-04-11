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


def _fetch_history_sync(wkn: str) -> dict[datetime.date, Decimal]:
    """Fetch 1Y of daily closing prices via yfinance (blocking).

    Returns a mapping of {date: close_price}.
    """
    import yfinance as yf  # type: ignore[import-untyped]

    hist = yf.Ticker(wkn.upper()).history(period="1y")
    if hist.empty:
        return {}
    result: dict[datetime.date, Decimal] = {}
    for ts, row in hist["Close"].items():
        date = ts.date() if hasattr(ts, "date") else ts
        result[date] = Decimal(str(round(float(row), 4)))
    return result


async def _fetch_history(wkn: str) -> dict[datetime.date, Decimal]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_fetch_history_sync, wkn))


async def refresh_price_cache(wkns: list[str], db: AsyncSession) -> None:
    """Fetch 1Y of daily closes for each WKN and upsert into PriceCache.

    Intended to be called on startup and once per day via the scheduler.
    """
    for wkn in wkns:
        try:
            history = await _fetch_history(wkn)
        except Exception:
            logger.exception("Failed to fetch history for %s", wkn)
            continue

        if not history:
            logger.warning("No history returned for %s", wkn)
            continue

        rows = [
            {"wkn": wkn.upper(), "date": date, "close_price": price}
            for date, price in history.items()
        ]
        stmt = (
            insert(PriceCache)
            .values(rows)
            .on_conflict_do_update(
                constraint="uq_price_cache_wkn_date",
                set_={"close_price": insert(PriceCache).excluded.close_price},
            )
        )
        await db.execute(stmt)

    await db.commit()
    logger.info("Price cache refreshed for %d WKN(s).", len(wkns))


async def get_price(wkn: str, date: datetime.date, db: AsyncSession) -> Decimal | None:
    """Return the cached closing price for *wkn* on *date*, or None."""
    result = await db.execute(
        select(PriceCache.close_price).where(
            PriceCache.wkn == wkn.upper(),
            PriceCache.date == date,
        )
    )
    row = result.scalar_one_or_none()
    return row


class StockPriceService:
    """Provides price and metadata lookups for stocks via yfinance."""

    async def get_current_price(self, wkn: str) -> Decimal | None:
        """Return the current price for *wkn*, or None if unavailable."""
        info = await fetch_stock_info(wkn)
        if info is None:
            return None
        return info.current_price

    async def get_company_name(self, wkn: str) -> str | None:
        """Return the company name for *wkn*, or None if the WKN is invalid."""
        info = await fetch_stock_info(wkn)
        if info is None:
            return None
        return info.name

    async def validate_wkn(self, wkn: str) -> bool:
        """Return True if *wkn* resolves to a known, non-delisted security."""
        info = await fetch_stock_info(wkn)
        return info is not None
