"""Service for importing broker PDF statements into the portfolio."""

from __future__ import annotations

import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock
from app.models.transaction import TX_SOURCE_PDF, TX_TYPE_BUY, Transaction
from app.services.holdings_service import recompute_holdings
from app.services.pdf_parser import BaseBrokerParser


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

        await db.flush()
        if affected_stock_ids:
            await recompute_holdings(db, affected_stock_ids)
        return processed

    async def _get_stock(self, db: AsyncSession, ticker: str) -> Stock | None:
        result = await db.execute(
            select(Stock).where(Stock.ticker == ticker.upper())
        )
        return result.scalar_one_or_none()
