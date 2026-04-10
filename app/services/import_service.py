"""Service for importing broker PDF statements into the portfolio."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.holding import Holding
from app.models.stock import Stock
from app.services.pdf_parser import BaseBrokerParser


class ImportService:
    """Upsert holdings extracted from a broker PDF into the database.

    For each ``(ticker, quantity)`` pair the parser returns:

    * If a :class:`~app.models.holding.Holding` already exists for that
      ticker its ``quantity`` is **increased** by the extracted amount.
    * If no holding exists yet, a new one is created.
    * Tickers without a matching :class:`~app.models.stock.Stock` row are
      silently skipped.
    """

    async def import_from_pdf(
        self,
        pdf_path: Path,
        parser: BaseBrokerParser,
        db: AsyncSession,
    ) -> list[tuple[str, Decimal]]:
        """Parse *pdf_path* and upsert the extracted holdings.

        Returns
        -------
        list[tuple[str, Decimal]]
            ``(ticker, quantity_added)`` pairs for every ticker that was
            successfully processed (i.e. had a matching Stock record).
        """
        pairs = parser.extract(pdf_path)
        processed: list[tuple[str, Decimal]] = []

        for ticker, qty in pairs:
            stock_row = await db.execute(
                select(Stock).where(Stock.ticker == ticker)
            )
            stock = stock_row.scalar_one_or_none()
            if stock is None:
                continue

            holding_row = await db.execute(
                select(Holding).where(Holding.stock_id == stock.id)
            )
            holding = holding_row.scalar_one_or_none()

            if holding is None:
                db.add(Holding(stock_id=stock.id, quantity=qty))
            else:
                holding.quantity += qty

            processed.append((ticker, qty))

        await db.flush()
        return processed
