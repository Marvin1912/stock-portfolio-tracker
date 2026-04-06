"""Stock lookup via yfinance."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from functools import partial


@dataclass
class StockInfo:
    ticker: str
    name: str
    currency: str
    current_price: Decimal | None


def _fetch_stock_info_sync(ticker: str) -> StockInfo | None:
    """Synchronous yfinance call — run in a thread executor."""
    import yfinance as yf  # local import to keep startup fast

    t = yf.Ticker(ticker.upper())
    info = t.info

    # yfinance returns a minimal dict for unknown tickers
    name = info.get("longName") or info.get("shortName")
    if not name:
        return None

    currency = info.get("currency") or "USD"
    price_raw = info.get("currentPrice") or info.get("regularMarketPrice")
    current_price = Decimal(str(price_raw)) if price_raw is not None else None

    return StockInfo(
        ticker=ticker.upper(),
        name=name,
        currency=currency,
        current_price=current_price,
    )


async def fetch_stock_info(ticker: str) -> StockInfo | None:
    """Return stock info from yfinance, or None if the ticker is invalid."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(_fetch_stock_info_sync, ticker))
