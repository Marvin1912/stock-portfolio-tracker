"""Service for generating monthly portfolio wealth reports."""

from __future__ import annotations

import calendar
import datetime
import logging
from dataclasses import dataclass
from decimal import Decimal

from jinja2 import Environment, PackageLoader, select_autoescape
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.price_cache import PriceCache
from app.models.stock import Stock
from app.services.fx_service import to_eur
from app.services.holdings_service import net_shares_as_of_date

logger = logging.getLogger(__name__)


@dataclass
class StockReportLine:
    """Per-stock data for the monthly report."""

    ticker: str
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

    async def get_available_months(
        self, db: AsyncSession
    ) -> list[tuple[int, int]]:
        """Return distinct (year, month) tuples that have price data, newest first.

        Only completed months (strictly before the current calendar month) are
        returned.
        """
        current_month_start = datetime.date.today().replace(day=1)
        subq = (
            select(func.date_trunc("month", PriceCache.date).label("month_start"))
            .where(PriceCache.date < current_month_start)
            .distinct()
            .subquery()
        )
        result = await db.execute(
            select(subq.c.month_start).order_by(subq.c.month_start.desc())
        )
        rows = result.scalars().all()
        return [(row.year, row.month) for row in rows]

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
        return await self._build_report(db, period_start, period_end)

    async def generate_report_for_month(
        self, db: AsyncSession, year: int, month: int
    ) -> MonthlyReportData | None:
        """Build the monthly report for an explicit calendar month.

        Returns ``None`` when there are no holdings.
        """
        period_start = datetime.date(year, month, 1)
        last_day = calendar.monthrange(year, month)[1]
        period_end = datetime.date(year, month, last_day)
        return await self._build_report(db, period_start, period_end)

    async def _build_report(
        self,
        db: AsyncSession,
        period_start: datetime.date,
        period_end: datetime.date,
    ) -> MonthlyReportData | None:
        """Core report-building logic shared by all public methods."""
        start_of_period = period_start - datetime.timedelta(days=1)

        # Historical quantities: what did the portfolio look like at the period
        # boundaries?  Using current holdings would misrepresent months where
        # shares were bought or sold mid-period.
        start_positions = await net_shares_as_of_date(db, start_of_period)
        end_positions = await net_shares_as_of_date(db, period_end)

        all_stock_ids = set(start_positions) | set(end_positions)
        if not all_stock_ids:
            return None

        stocks_result = await db.execute(
            select(Stock).where(Stock.id.in_(list(all_stock_ids)))
        )
        stocks: dict[int, Stock] = {s.id: s for s in stocks_result.scalars().all()}

        tickers = [s.ticker for s in stocks.values()]

        price_rows = await db.execute(
            select(PriceCache.ticker, PriceCache.date, PriceCache.close_price)
            .where(
                PriceCache.ticker.in_(tickers),
                PriceCache.date >= period_start,
                PriceCache.date <= period_end,
            )
        )

        prices: dict[str, dict[datetime.date, Decimal]] = {}
        for ticker, date, close_price in price_rows:
            prices.setdefault(ticker, {})[date] = close_price

        lines: list[StockReportLine] = []
        total_value_1st: Decimal | None = None
        total_value_last: Decimal | None = None

        for sid in sorted(all_stock_ids):
            stock = stocks.get(sid)
            if stock is None:
                continue

            ticker = stock.ticker
            currency = stock.currency
            qty_start = start_positions.get(sid, Decimal("0"))
            qty_end = end_positions.get(sid, Decimal("0"))

            ticker_prices = prices.get(ticker, {})

            price_1st: Decimal | None = None
            price_last: Decimal | None = None

            if ticker_prices:
                price_1st = to_eur(ticker_prices[min(ticker_prices)], currency)
                price_last = to_eur(ticker_prices[max(ticker_prices)], currency)

            value_1st = qty_start * price_1st if price_1st is not None else None
            value_last = qty_end * price_last if price_last is not None else None

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
                    ticker=ticker,
                    name=stock.name,
                    quantity=qty_end,
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
