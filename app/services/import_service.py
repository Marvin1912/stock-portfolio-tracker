"""Service for importing broker PDF statements into the portfolio."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock
from app.models.transaction import TX_SOURCE_PDF, TX_TYPE_BUY, Transaction
from app.services import chart_cache
from app.services.comdirect_parser import ParsedTrade
from app.services.comdirect_ref import build_pdf_external_uuid
from app.services.holdings_service import recompute_holdings
from app.services.pdf_parser import BaseBrokerParser
from app.services.price_service import ensure_prices_cached

TradeImportStatus = Literal["created", "duplicate", "unknown_ticker"]


class ImportService:
    """Import broker-PDF holdings as transactions and rebuild the snapshot.

    Each ``(ticker, quantity)`` pair becomes a ``BUY`` transaction with
    ``source = PDF`` and a synthetic ``external_uuid`` of the form
    ``pdf:{filename}:{idx}``.  Re-importing the same file is a no-op
    because the UUID collides.

    After inserting the transactions the affected holdings are recomputed
    via :func:`recompute_holdings`.
    """

    async def import_from_pdf(
        self,
        pdf_path: Path,
        parser: BaseBrokerParser,
        db: AsyncSession,
    ) -> list[tuple[str, Decimal]]:
        """Parse *pdf_path* and persist the extracted holdings."""
        pairs = parser.extract(pdf_path)
        return await self.import_from_holdings(pairs, db, source_file=pdf_path.name)

    async def import_from_holdings(
        self,
        pairs: list[tuple[str, Decimal]],
        db: AsyncSession,
        *,
        source_file: str = "manual",
    ) -> list[tuple[str, Decimal]]:
        """Insert a transaction per pair, then rebuild the holding snapshot."""
        processed: list[tuple[str, Decimal]] = []
        affected_stock_ids: set[int] = set()
        affected_tickers: set[str] = set()
        now = datetime.datetime.now(datetime.UTC)

        for idx, (ticker, qty) in enumerate(pairs):
            stock = await self._get_stock(db, ticker)
            if stock is None:
                continue

            external_uuid = f"pdf:{source_file}:{idx}"
            existing = await db.execute(
                select(Transaction.id).where(
                    Transaction.external_uuid == external_uuid
                )
            )
            if existing.scalar_one_or_none() is not None:
                processed.append((ticker, qty))
                affected_stock_ids.add(stock.id)
                affected_tickers.add(stock.ticker)
                continue

            db.add(
                Transaction(
                    external_uuid=external_uuid,
                    stock_id=stock.id,
                    date=now,
                    type=TX_TYPE_BUY,
                    shares=qty,
                    amount=Decimal("0"),
                    currency=stock.currency,
                    fee=Decimal("0"),
                    tax=Decimal("0"),
                    note=f"Imported from {source_file}",
                    source=TX_SOURCE_PDF,
                )
            )
            processed.append((ticker, qty))
            affected_stock_ids.add(stock.id)
            affected_tickers.add(stock.ticker)

        await db.flush()
        if affected_stock_ids:
            await recompute_holdings(db, affected_stock_ids)
            await ensure_prices_cached(affected_tickers, db)
            # Holdings changed — bust the cached portfolio summary/charts so the
            # main page reflects the new quantities instead of stale cache.
            chart_cache.invalidate()
        return processed

    async def check_is_duplicate(
        self,
        trade: ParsedTrade,
        ticker: str,
        db: AsyncSession,
    ) -> bool | None:
        """Check whether *trade* would be a duplicate without inserting anything.

        Returns ``True`` (duplicate), ``False`` (new), or ``None`` (unknown
        ticker).  Uses the same exact-UUID / fuzzy-same-day logic as
        :meth:`import_trade`.
        """
        stock = await self._get_stock(db, ticker)
        if stock is None:
            return None

        if trade.order_ref:
            external_uuid = build_pdf_external_uuid(trade.broker, trade.order_ref)
            existing = await db.execute(
                select(Transaction.id).where(
                    Transaction.external_uuid == external_uuid
                )
            )
            return existing.scalar_one_or_none() is not None
        return await self._find_duplicate_trade(db, stock.id, trade)

    async def import_trade(
        self,
        trade: ParsedTrade,
        ticker: str,
        db: AsyncSession,
        *,
        source_file: str = "comdirect",
    ) -> TradeImportStatus:
        """Persist a single comdirect trade as a full BUY/SELL transaction.

        The security must already be tracked under *ticker* (resolved from the
        PDF's WKN/ISIN); unknown tickers are skipped, mirroring
        :meth:`import_from_holdings`.  The transaction captures the gross value,
        fees, taxes and the real trade date.

        Duplicate protection runs across *all* sources, not just prior PDF
        imports: the same purchase may already be in the database from a
        Portfolio Performance XML import. When the trade carries a comdirect
        ``order_ref`` we build the shared ``pdf:comdirect:{ref}`` key (the same
        one the XML importer derives from the note's *Ordernummer*) and look it
        up exactly — so an XML-first then PDF-second import dedupes
        deterministically without tripping the unique constraint, and two
        genuinely distinct same-day trades keep distinct keys. Only when no
        order ref is available do we fall back to the fuzzy same-day probe in
        :meth:`_find_duplicate_trade`.

        Returns ``"created"`` when a new transaction was inserted,
        ``"duplicate"`` when a matching one already exists, or
        ``"unknown_ticker"`` when the security is not tracked.
        """
        stock = await self._get_stock(db, ticker)
        if stock is None:
            return "unknown_ticker"

        if trade.order_ref:
            external_uuid = build_pdf_external_uuid(trade.broker, trade.order_ref)
            existing = await db.execute(
                select(Transaction.id).where(
                    Transaction.external_uuid == external_uuid
                )
            )
            if existing.scalar_one_or_none() is not None:
                return "duplicate"
        else:
            # No stable order reference — fall back to the fuzzy same-day probe.
            if await self._find_duplicate_trade(db, stock.id, trade):
                return "duplicate"
            external_uuid = build_pdf_external_uuid(
                trade.broker, f"{trade.isin or trade.wkn}:{trade.date.date()}"
            )

        db.add(
            Transaction(
                external_uuid=external_uuid,
                stock_id=stock.id,
                date=trade.date,
                type=trade.trade_type,
                shares=trade.shares,
                amount=trade.amount,
                currency=trade.currency or stock.currency,
                fee=trade.fee,
                tax=trade.tax,
                note=f"Imported from {source_file}",
                source=TX_SOURCE_PDF,
            )
        )
        await db.flush()
        await recompute_holdings(db, {stock.id})
        await ensure_prices_cached([stock.ticker], db)
        # Holdings changed — bust the cached portfolio summary/charts so the
        # main page reflects the new quantities instead of stale cache.
        chart_cache.invalidate()
        return "created"

    async def _find_duplicate_trade(
        self,
        db: AsyncSession,
        stock_id: int,
        trade: ParsedTrade,
    ) -> bool:
        """Return True if an equivalent trade already exists for *stock_id*.

        Matches on stock + type + share count within the same UTC calendar day
        as ``trade.date``.  Amount and time-of-day are deliberately excluded:
        an XML-imported buy stores Portfolio Performance's total (gross + fees)
        and the execution time, whereas the comdirect PDF carries the gross
        ``Kurswert`` and only the trade date — so neither would match exactly.
        """
        trade_date = trade.date
        if trade_date.tzinfo is None:
            trade_date = trade_date.replace(tzinfo=datetime.UTC)
        day_start = trade_date.astimezone(datetime.UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + datetime.timedelta(days=1)

        existing = await db.execute(
            select(Transaction.id)
            .where(
                and_(
                    Transaction.stock_id == stock_id,
                    Transaction.type == trade.trade_type,
                    Transaction.shares == trade.shares,
                    Transaction.date >= day_start,
                    Transaction.date < day_end,
                )
            )
            .limit(1)
        )
        return existing.scalar_one_or_none() is not None

    async def _get_stock(self, db: AsyncSession, ticker: str) -> Stock | None:
        result = await db.execute(
            select(Stock).where(Stock.ticker == ticker.upper())
        )
        return result.scalar_one_or_none()
