"""Service for calculating portfolio values."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.holding import Holding
from app.schemas.holdings import HoldingSummaryItem, PortfolioSummary


class PortfolioService:
    """Calculates current market values for portfolio holdings."""

    async def get_summary(self, db: AsyncSession) -> PortfolioSummary:
        """Return per-holding market values and total portfolio value.

        Holdings without a ``current_price`` contribute ``None`` for their
        value and are excluded from the ``total_value`` sum.
        """
        rows = await db.execute(select(Holding).join(Holding.stock))
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
