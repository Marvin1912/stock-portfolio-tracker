"""Service for generating monthly portfolio wealth reports."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

from jinja2 import Environment, PackageLoader, select_autoescape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.holding import Holding
from app.models.price_cache import PriceCache

logger = logging.getLogger(__name__)


@dataclass
class StockReportLine:
    """Per-stock data for the monthly report."""

    wkn: str
    name: str
    quantity: Decimal
    price_1st: Decimal | None
    price_last: Decimal | None
    value_1st: Decimal | None
    value_last: Decimal | None
    delta_eur: Decimal | None
    delta_pct: Decimal | None


@dataclass
class MonthlyReportData:
    """Aggregated data for the monthly wealth report."""

    month_label: str  # e.g. "March 2026"
    period_start: datetime.date
    period_end: datetime.date
    lines: list[StockReportLine]
    total_value_1st: Decimal | None
    total_value_last: Decimal | None
    total_delta_eur: Decimal | None
    total_delta_pct: Decimal | None


class ReportService:
    """Generates the monthly portfolio wealth report."""

    async def generate_monthly_report(
        self, db: AsyncSession, reference_date: datetime.date | None = None
    ) -> MonthlyReportData | None:
        """Build the monthly report for the month preceding *reference_date*.

        Uses prices stored in ``PriceCache`` — no live API calls are made.
        Returns ``None`` when there are no holdings.

        Args:
            db: Async database session.
            reference_date: Date used to determine which month to report on.
                Defaults to today.
        """
        today = reference_date or datetime.date.today()
        period_end = today.replace(day=1) - datetime.timedelta(days=1)
        period_start = period_end.replace(day=1)

        rows = await db.execute(select(Holding).options(selectinload(Holding.stock)))
        holdings = rows.scalars().all()

        if not holdings:
            return None

        wkns = [h.stock.wkn for h in holdings]

        # Fetch all cached prices for those WKNs within the previous month.
        price_rows = await db.execute(
            select(PriceCache.wkn, PriceCache.date, PriceCache.close_price)
            .where(
                PriceCache.wkn.in_(wkns),
                PriceCache.date >= period_start,
                PriceCache.date <= period_end,
            )
        )

        # Build {wkn: {date: price}} mapping.
        prices: dict[str, dict[datetime.date, Decimal]] = {}
        for wkn, date, close_price in price_rows:
            prices.setdefault(wkn, {})[date] = close_price

        lines: list[StockReportLine] = []
        total_value_1st: Decimal | None = None
        total_value_last: Decimal | None = None

        for h in holdings:
            wkn = h.stock.wkn
            wkn_prices = prices.get(wkn, {})

            price_1st: Decimal | None = None
            price_last: Decimal | None = None

            if wkn_prices:
                price_1st = wkn_prices[min(wkn_prices)]
                price_last = wkn_prices[max(wkn_prices)]

            value_1st = h.quantity * price_1st if price_1st is not None else None
            value_last = h.quantity * price_last if price_last is not None else None

            delta_eur: Decimal | None = None
            delta_pct: Decimal | None = None
            if value_1st is not None and value_last is not None:
                delta_eur = value_last - value_1st
                if value_1st != Decimal("0"):
                    delta_pct = (delta_eur / value_1st * Decimal("100")).quantize(
                        Decimal("0.01")
                    )

            if value_1st is not None:
                total_value_1st = (total_value_1st or Decimal("0")) + value_1st
            if value_last is not None:
                total_value_last = (total_value_last or Decimal("0")) + value_last

            lines.append(
                StockReportLine(
                    wkn=h.stock.wkn,
                    name=h.stock.name,
                    quantity=h.quantity,
                    price_1st=price_1st,
                    price_last=price_last,
                    value_1st=value_1st,
                    value_last=value_last,
                    delta_eur=delta_eur,
                    delta_pct=delta_pct,
                )
            )

        total_delta_eur: Decimal | None = None
        total_delta_pct: Decimal | None = None
        if total_value_1st is not None and total_value_last is not None:
            total_delta_eur = total_value_last - total_value_1st
            if total_value_1st != Decimal("0"):
                total_delta_pct = (
                    total_delta_eur / total_value_1st * Decimal("100")
                ).quantize(Decimal("0.01"))

        return MonthlyReportData(
            month_label=period_start.strftime("%B %Y"),
            period_start=period_start,
            period_end=period_end,
            lines=lines,
            total_value_1st=total_value_1st,
            total_value_last=total_value_last,
            total_delta_eur=total_delta_eur,
            total_delta_pct=total_delta_pct,
        )

    def render_html(self, data: MonthlyReportData) -> str:
        """Render *data* into an HTML email string using the Jinja2 template."""
        env = Environment(
            loader=PackageLoader("app", "templates"),
            autoescape=select_autoescape(["html"]),
        )
        template = env.get_template("email/report.html")
        return template.render(report=data)
