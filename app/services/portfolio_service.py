"""Service for calculating portfolio values."""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.holding import Holding
from app.models.price_cache import PriceCache
from app.schemas.holdings import HoldingSummaryItem, PortfolioSummary


class PortfolioService:
    """Calculates current market values for portfolio holdings."""

    async def get_summary(self, db: AsyncSession) -> PortfolioSummary:
        """Return per-holding market values and total portfolio value.

        Holdings without a ``current_price`` contribute ``None`` for their
        value and are excluded from the ``total_value`` sum.
        """
        rows = await db.execute(select(Holding).options(selectinload(Holding.stock)))
        holdings = rows.scalars().all()

        items: list[HoldingSummaryItem] = []
        total_value: Decimal | None = None

        for h in holdings:
            current_value: Decimal | None = None
            if h.stock.current_price is not None:
                current_value = h.quantity * h.stock.current_price
                total_value = (total_value or Decimal("0")) + current_value

            items.append(
                HoldingSummaryItem(
                    id=h.id,
                    ticker=h.stock.ticker,
                    name=h.stock.name,
                    quantity=h.quantity,
                    current_price=h.stock.current_price,
                    current_value=current_value,
                )
            )

        return PortfolioSummary(holdings=items, total_value=total_value)

    async def get_performance_history(
        self, db: AsyncSession
    ) -> list[tuple[datetime.date, Decimal]]:
        """Return daily total portfolio values for the past year.

        For each date in PriceCache, the portfolio value is calculated as
        sum(quantity × close_price) across all holdings that have a cached
        price on that date.  Dates with no price data are omitted.
        """
        rows = await db.execute(select(Holding).options(selectinload(Holding.stock)))
        holdings = rows.scalars().all()

        if not holdings:
            return []

        tickers = [h.stock.ticker for h in holdings]
        qty_by_ticker = {h.stock.ticker: h.quantity for h in holdings}

        one_year_ago = datetime.date.today() - datetime.timedelta(days=365)
        price_rows = await db.execute(
            select(PriceCache.ticker, PriceCache.date, PriceCache.close_price)
            .where(
                PriceCache.ticker.in_(tickers),
                PriceCache.date >= one_year_ago,
            )
            .order_by(PriceCache.date)
        )

        prices_by_date: dict[datetime.date, dict[str, Decimal]] = {}
        for ticker, date, close_price in price_rows:
            prices_by_date.setdefault(date, {})[ticker] = close_price

        performance: list[tuple[datetime.date, Decimal]] = []
        for date in sorted(prices_by_date):
            day_prices = prices_by_date[date]
            total = sum((qty_by_ticker[t] * p for t, p in day_prices.items()), Decimal("0"))
            performance.append((date, total))

        return performance
