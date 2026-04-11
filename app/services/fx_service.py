"""Foreign exchange rate service for EUR conversion.

Fetches live exchange rates via yfinance and caches them in memory.
Rates are refreshed once daily by the scheduler.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from functools import partial

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


async def refresh_fx_rates(currencies: list[str]) -> None:
    """Refresh the in-memory FX cache for the given currency codes.

    EUR is always set to 1.0.  For all other currencies the rate is
    fetched from yfinance and stored in *_fx_cache*.
    """
    loop = asyncio.get_running_loop()
    _fx_cache["EUR"] = Decimal("1")

    for currency in currencies:
        cu = currency.upper()
        if cu == "EUR":
            continue
        try:
            rate = await loop.run_in_executor(None, partial(_fetch_rate_sync, cu))
            if rate is not None:
                _fx_cache[cu] = rate
                logger.debug("FX rate updated: EUR/%s = %s", cu, rate)
            else:
                logger.warning("No FX rate returned for %s", cu)
        except Exception:
            logger.exception("Failed to fetch FX rate for %s", cu)

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
