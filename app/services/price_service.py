"""Class-based stock price service wrapping yfinance."""

from __future__ import annotations

from decimal import Decimal

from app.services.stock_lookup import fetch_stock_info


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
