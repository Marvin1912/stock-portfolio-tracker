"""Foreign exchange rate service for EUR conversion.

Fetches live exchange rates via yfinance and caches them in memory.
Rates are refreshed once daily by the scheduler and persisted to the
``finance.fx_rate`` table so the cache can be warmed on startup and
used as a fallback when yfinance is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from functools import partial

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fx_rate import FxRate

logger = logging.getLogger(__name__)

# In-memory cache: currency code -> EUR/{CURRENCY}=X rate
# e.g. "USD" -> Decimal("1.10") means 1 EUR = 1.10 USD
_fx_cache: dict[str, Decimal] = {}


def _fetch_rate_sync(currency: str) -> Decimal | None:
    """Fetch the EUR/{CURRENCY} rate from yfinance (blocking).

    Uses the ticker ``EUR{CURRENCY}=X`` (e.g. ``EURUSD=X``).
    Returns the latest close price, or None if unavailable.
    """
    import yfinance as yf  # type: ignore[import-untyped]

    ticker = f"EUR{currency.upper()}=X"
    hist = yf.Ticker(ticker).history(period="1d")
    if hist.empty:
        return None
    close = float(hist["Close"].iloc[-1])
    return Decimal(str(round(close, 6)))


async def load_fx_cache_from_db(db: AsyncSession) -> int:
    """Populate the in-memory FX cache from the ``finance.fx_rate`` table.

    Returns the number of rates loaded (EUR is always set to 1 and is
    not counted).
    """
    _fx_cache["EUR"] = Decimal("1")
    result = await db.execute(select(FxRate.currency, FxRate.rate))
    loaded = 0
    for currency, rate in result.all():
        cu = currency.upper()
        if cu == "EUR":
            continue
        _fx_cache[cu] = rate
        loaded += 1
    logger.info("FX cache warmed from DB with %d rate(s).", loaded)
    return loaded


async def _persist_rate(db: AsyncSession, currency: str, rate: Decimal) -> None:
    """Upsert the latest rate for *currency* into ``finance.fx_rate``."""
    stmt = (
        insert(FxRate)
        .values(currency=currency, rate=rate)
        .on_conflict_do_update(
            index_elements=[FxRate.currency],
            set_={"rate": rate, "updated_at": func.now()},
        )
    )
    await db.execute(stmt)


async def _fallback_from_db(db: AsyncSession, currency: str) -> Decimal | None:
    """Load the last persisted rate for *currency* from the DB and cache it."""
    result = await db.execute(
        select(FxRate.rate).where(FxRate.currency == currency)
    )
    rate = result.scalar_one_or_none()
    if rate is not None:
        _fx_cache[currency] = rate
        logger.warning(
            "FX fallback: using persisted rate for %s (rate=%s).", currency, rate
        )
    return rate


async def refresh_fx_rates(currencies: list[str], db: AsyncSession) -> None:
    """Refresh the in-memory FX cache and persist rates to the DB.

    EUR is always set to 1.0.  For all other currencies the rate is
    fetched from yfinance and, on success, persisted to ``finance.fx_rate``.
    When a fetch fails (empty result or exception), the last persisted
    rate is loaded from the DB into the cache as a fallback.
    """
    loop = asyncio.get_running_loop()
    _fx_cache["EUR"] = Decimal("1")

    for currency in currencies:
        cu = currency.upper()
        if cu == "EUR":
            continue
        rate: Decimal | None = None
        try:
            rate = await loop.run_in_executor(None, partial(_fetch_rate_sync, cu))
        except Exception:
            logger.exception("Failed to fetch FX rate for %s", cu)

        if rate is not None:
            _fx_cache[cu] = rate
            logger.debug("FX rate updated: EUR/%s = %s", cu, rate)
            try:
                await _persist_rate(db, cu, rate)
            except Exception:
                logger.exception("Failed to persist FX rate for %s", cu)
        else:
            logger.warning("No FX rate returned for %s, attempting DB fallback.", cu)
            await _fallback_from_db(db, cu)

    try:
        await db.commit()
    except Exception:
        logger.exception("Failed to commit FX rate updates.")
        await db.rollback()

    logger.info("FX rates refreshed for %d currency/ies.", len(currencies))


def to_eur(amount: Decimal, currency: str) -> Decimal:
    """Convert *amount* from *currency* to EUR using the cached rate.

    If *currency* is already EUR the amount is returned unchanged.
    If no cached rate is available the amount is returned unchanged
    (graceful fallback).
    """
    currency = currency.upper()
    if currency == "EUR":
        return amount
    rate = _fx_cache.get(currency)
    if rate is None:
        logger.warning("No cached FX rate for %s, returning amount unchanged.", currency)
        return amount
    # EURUSD=X gives USD per 1 EUR, so: amount_eur = amount_usd / rate
    return amount / rate
