"""Service for calculating portfolio values."""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.holding import Holding
from app.models.price_cache import PriceCache
from app.models.stock import Stock
from app.models.transaction import Transaction
from app.schemas.holdings import HoldingSummaryItem, PortfolioSummary
from app.services.fx_service import to_eur

_POSITION_TYPES = ("BUY", "SELL", "TRANSFER_IN", "TRANSFER_OUT")
_POSITIVE_TYPES = {"BUY", "TRANSFER_IN"}


class PortfolioService:
    """Calculates current market values for portfolio holdings."""

    async def _latest_close_prices(
        self, db: AsyncSession, tickers: list[str]
    ) -> dict[str, Decimal]:
        """Return ``{ticker: latest close_price}`` from PriceCache."""
        if not tickers:
            return {}
        stmt = (
            select(PriceCache.ticker, PriceCache.close_price)
            .distinct(PriceCache.ticker)
            .where(PriceCache.ticker.in_(tickers))
            .order_by(PriceCache.ticker, PriceCache.date.desc())
        )
        result = await db.execute(stmt)
        return {ticker: price for ticker, price in result.all()}

    async def get_summary(self, db: AsyncSession) -> PortfolioSummary:
        """Return per-holding market values and total portfolio value.

        Uses the latest close price from ``PriceCache`` so the total stays
        consistent with the performance chart.  Holdings without a cached
        price contribute ``None`` for their value and are excluded from
        the ``total_value`` sum.
        """
        rows = await db.execute(select(Holding).options(selectinload(Holding.stock)))
        holdings = rows.scalars().all()

        tickers = [h.stock.ticker.upper() for h in holdings]
        latest_prices = await self._latest_close_prices(db, tickers)

        items: list[HoldingSummaryItem] = []
        total_value: Decimal | None = None

        for h in holdings:
            current_value: Decimal | None = None
            eur_price: Decimal | None = None
            close_price = latest_prices.get(h.stock.ticker.upper())
            if close_price is not None and close_price.is_finite():
                eur_price = to_eur(close_price, h.stock.currency)
                current_value = h.quantity * eur_price
                total_value = (total_value or Decimal("0")) + current_value

            items.append(
                HoldingSummaryItem(
                    id=h.id,
                    ticker=h.stock.ticker,
                    name=h.stock.name,
                    asset_type=h.stock.asset_type,
                    quantity=h.quantity,
                    current_price=eur_price,
                    current_value=current_value,
                )
            )

        return PortfolioSummary(holdings=items, total_value=total_value)

    async def get_performance_history(
        self, db: AsyncSession
    ) -> list[tuple[datetime.date, Decimal]]:
        """Return daily total portfolio values for the past year.

        Replays the transaction history so each chart date reflects the
        positions that were actually held on that day — not today's
        snapshot projected backwards.

        Algorithm
        ---------
        1. Load every position-affecting transaction (BUY, SELL,
           TRANSFER_IN, TRANSFER_OUT) ordered by date, per stock.
        2. Walk the price-cache dates forward.  For each date *d* we
           advance a per-stock pointer over events with ``event.date ≤ d``
           and add the signed share delta to a running ``positions[sid]``
           tally — so each event is visited exactly once across the walk.
        3. Multiply the running positions by the cached close price on
           date *d*, convert to EUR via the FX cache, and sum.
        """
        event_rows = await db.execute(
            select(
                Transaction.stock_id,
                Transaction.date,
                Transaction.type,
                Transaction.shares,
            )
            .where(Transaction.stock_id.is_not(None))
            .where(Transaction.type.in_(_POSITION_TYPES))
            .order_by(Transaction.stock_id, Transaction.date)
        )

        events_by_stock: dict[int, list[tuple[datetime.date, Decimal]]] = {}
        for stock_id, dt, tx_type, shares in event_rows:
            if stock_id is None:
                continue
            delta = shares if tx_type in _POSITIVE_TYPES else -shares
            event_date = dt.date() if hasattr(dt, "date") else dt
            events_by_stock.setdefault(stock_id, []).append((event_date, delta))

        if not events_by_stock:
            return []

        stock_ids = list(events_by_stock.keys())
        stock_rows = await db.execute(
            select(Stock.id, Stock.ticker, Stock.currency).where(
                Stock.id.in_(stock_ids)
            )
        )
        stock_info: dict[int, tuple[str, str]] = {
            sid: (ticker.upper(), currency) for sid, ticker, currency in stock_rows
        }
        tickers_upper = [info[0] for info in stock_info.values()]
        if not tickers_upper:
            return []

        one_year_ago = datetime.date.today() - datetime.timedelta(days=365)
        price_rows = await db.execute(
            select(PriceCache.ticker, PriceCache.date, PriceCache.close_price)
            .where(
                PriceCache.ticker.in_(tickers_upper),
                PriceCache.date >= one_year_ago,
            )
            .order_by(PriceCache.date)
        )

        prices_by_date: dict[datetime.date, dict[str, Decimal]] = {}
        for ticker, dt, close_price in price_rows:
            if close_price.is_finite():
                prices_by_date.setdefault(dt, {})[ticker.upper()] = close_price

        if not prices_by_date:
            return []

        positions: dict[int, Decimal] = {sid: Decimal("0") for sid in stock_ids}
        event_ptrs: dict[int, int] = {sid: 0 for sid in stock_ids}

        performance: list[tuple[datetime.date, Decimal]] = []
        for date in sorted(prices_by_date):
            for sid, events in events_by_stock.items():
                ptr = event_ptrs[sid]
                while ptr < len(events) and events[ptr][0] <= date:
                    positions[sid] += events[ptr][1]
                    ptr += 1
                event_ptrs[sid] = ptr

            day_prices = prices_by_date[date]
            total = Decimal("0")
            for sid, qty in positions.items():
                if qty == 0:
                    continue
                ticker, currency = stock_info[sid]
                price = day_prices.get(ticker)
                if price is None:
                    continue
                total += qty * to_eur(price, currency)

            performance.append((date, total))

        return performance
