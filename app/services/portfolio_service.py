"""Service for calculating portfolio values."""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.holding import Holding
from app.models.price_cache import PriceCache
from app.models.stock import Stock
from app.models.transaction import (
    TX_TYPE_BUY,
    TX_TYPE_DIVIDEND,
    TX_TYPE_FEE,
    TX_TYPE_SELL,
    TX_TYPE_TAX,
    Transaction,
)
from app.schemas.holdings import HoldingSummaryItem, PortfolioSummary
from app.services.fx_service import to_eur

_POSITION_TYPES = ("BUY", "SELL", "TRANSFER_IN", "TRANSFER_OUT")
_POSITIVE_TYPES = {"BUY", "TRANSFER_IN"}

# Transaction types that move cash in or out of the portfolio, used to derive
# the net-invested baseline for the gain/loss series.  TRANSFER_IN/OUT are
# excluded — they shift securities, not cash (and don't occur in this data set).
_CASH_FLOW_TYPES = (
    TX_TYPE_BUY,
    TX_TYPE_SELL,
    TX_TYPE_DIVIDEND,
    TX_TYPE_FEE,
    TX_TYPE_TAX,
)


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
            .where(
                PriceCache.ticker.in_(tickers),
                # Ignore any non-finite (NaN) close so a bad bar can't become
                # the "latest" price and blank the holding's value.
                PriceCache.close_price != Decimal("NaN"),
            )
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
        self, db: AsyncSession, since: datetime.date | None = None
    ) -> list[tuple[datetime.date, Decimal]]:
        """Return daily total portfolio values.

        Spans the trailing year by default; pass *since* to start the walk on
        an earlier date (e.g. the first transaction for the gain/loss chart).

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

        lower_bound = since or (datetime.date.today() - datetime.timedelta(days=365))
        price_rows = await db.execute(
            select(PriceCache.ticker, PriceCache.date, PriceCache.close_price)
            .where(
                PriceCache.ticker.in_(tickers_upper),
                PriceCache.date >= lower_bound,
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

        # Carry each ticker's most recent close forward.  Holdings have
        # different date coverage in the cache (different exchange calendars,
        # plus crypto trading on weekends), so without forward-fill a date
        # where some ticker lacks a close would drop that position from the
        # total — producing a sawtooth.  See issue with the Performance chart.
        last_price: dict[str, Decimal] = {}

        performance: list[tuple[datetime.date, Decimal]] = []
        for date in sorted(prices_by_date):
            for sid, events in events_by_stock.items():
                ptr = event_ptrs[sid]
                while ptr < len(events) and events[ptr][0] <= date:
                    positions[sid] += events[ptr][1]
                    ptr += 1
                event_ptrs[sid] = ptr

            for tkr, close in prices_by_date[date].items():
                last_price[tkr] = close

            total = Decimal("0")
            for sid, qty in positions.items():
                if qty == 0:
                    continue
                ticker, currency = stock_info[sid]
                price = last_price.get(ticker)
                if price is None:
                    # Ticker has no cached close yet — value genuinely unknown.
                    continue
                total += qty * to_eur(price, currency)

            performance.append((date, total))

        return performance

    @staticmethod
    def _net_invested_delta(
        tx_type: str, amount: Decimal, fee: Decimal, tax: Decimal
    ) -> Decimal:
        """Signed contribution of a transaction to *net invested* (native ccy).

        Positive = cash flowed out of your pocket into the portfolio (raises
        net invested); negative = cash flowed back to you (lowers it).

        - BUY: ``amount + fee + tax`` (the full cost paid).
        - SELL: ``-(amount - fee)`` (net proceeds received).
        - DIVIDEND: ``-amount`` (cash received).
        - FEE / TAX: ``amount + fee + tax`` (standalone cost; the value sits in
          whichever column the importer used, so summing all three is robust).
        """
        if tx_type == TX_TYPE_BUY:
            return amount + fee + tax
        if tx_type == TX_TYPE_SELL:
            return -(amount - fee)
        if tx_type == TX_TYPE_DIVIDEND:
            return -amount
        if tx_type in (TX_TYPE_FEE, TX_TYPE_TAX):
            return amount + fee + tax
        return Decimal("0")

    async def get_gain_loss_history(
        self, db: AsyncSession
    ) -> list[tuple[datetime.date, Decimal]]:
        """Return the daily Total P/L series since the first transaction.

        ``P/L(t) = market_value(t) − net_invested(t)``, where *net invested* is
        the running sum of cash paid in (BUY cost, standalone FEE/TAX) minus
        cash taken out (SELL proceeds, DIVIDEND).  This keeps realized gains
        correct: a fully-sold position has zero market value but its profit
        lives on as a negative net-invested balance, so the line stays up.

        All amounts are FX-converted to EUR.  The series shares the
        forward-filled market-value walk used by the Performance chart, but
        spans from the earliest transaction rather than the trailing year.
        """
        since = await self._earliest_transaction_date(db)
        market_values = await self.get_performance_history(db, since=since)
        if not market_values:
            return []

        flow_rows = await db.execute(
            select(
                Transaction.date,
                Transaction.type,
                Transaction.amount,
                Transaction.fee,
                Transaction.tax,
                Transaction.currency,
            )
            .where(Transaction.type.in_(_CASH_FLOW_TYPES))
            .order_by(Transaction.date)
        )

        flows: list[tuple[datetime.date, Decimal]] = []
        for dt, tx_type, amount, fee, tax, currency in flow_rows:
            delta = self._net_invested_delta(tx_type, amount, fee, tax)
            if delta == 0:
                continue
            flow_date = dt.date() if hasattr(dt, "date") else dt
            flows.append((flow_date, to_eur(delta, currency)))
        flows.sort(key=lambda f: f[0])

        gain_loss: list[tuple[datetime.date, Decimal]] = []
        ptr = 0
        net_invested = Decimal("0")
        for date, market_value in market_values:
            while ptr < len(flows) and flows[ptr][0] <= date:
                net_invested += flows[ptr][1]
                ptr += 1
            gain_loss.append((date, market_value - net_invested))

        return gain_loss

    async def _earliest_transaction_date(
        self, db: AsyncSession
    ) -> datetime.date | None:
        """Return the date of the earliest stock transaction, or None."""
        result = await db.execute(
            select(func.min(Transaction.date)).where(
                Transaction.stock_id.is_not(None)
            )
        )
        earliest = result.scalar_one_or_none()
        if earliest is None:
            return None
        return earliest.date() if hasattr(earliest, "date") else earliest
