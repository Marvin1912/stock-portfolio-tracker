"""APScheduler integration for the Stock Portfolio Tracker.

Provides two scheduled jobs:
- Daily price cache refresh at 07:00
- Monthly report generation on the 1st of each month at 08:00
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models.stock import Stock
from app.services.fx_service import refresh_fx_rates
from app.services.price_service import refresh_price_cache
from app.services.report_service import ReportService

logger = logging.getLogger(__name__)


async def run_price_cache_refresh(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Fetch all tracked tickers and refresh the price cache."""
    async with session_factory() as db:
        tickers_result = await db.execute(select(Stock.ticker))
        tickers = list(tickers_result.scalars().all())

    if not tickers:
        logger.info("Price cache refresh: no tickers to refresh.")
        return

    async with session_factory() as db:
        await refresh_price_cache(tickers, db)

    logger.info("Price cache refresh complete for %d ticker(s).", len(tickers))


async def run_fx_rate_refresh(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Fetch all distinct currencies from tracked stocks and refresh FX cache."""
    async with session_factory() as db:
        currencies_result = await db.execute(select(Stock.currency).distinct())
        currencies = list(currencies_result.scalars().all())

    if not currencies:
        logger.info("FX rate refresh: no currencies to refresh.")
        return

    await refresh_fx_rates(currencies)
    logger.info("FX rate refresh complete for %d currency/ies.", len(currencies))


async def run_monthly_report(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Generate the monthly report for the previous full calendar month.

    Covers the previous full calendar month (1st to last day).
    Logs a warning and returns early if there are no holdings.
    """
    async with session_factory() as db:
        report_data = await ReportService().generate_monthly_report(db)

    if report_data is None:
        logger.warning("Monthly report: no holdings found, skipping.")
        return

    logger.info(
        "Monthly report generated for %s: total_value_last=%s",
        report_data.month_label,
        report_data.total_value_last,
    )


def create_scheduler(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIOScheduler:
    """Build and return a configured AsyncIOScheduler (not yet started).

    Schedule 1 — daily price cache refresh at 07:00.
    Schedule 2 — daily FX rate refresh at 07:05.
    Schedule 3 — monthly report generation on the 1st of each month at 08:00.

    Args:
        settings: Application settings used for timezone configuration.
        session_factory: Async session factory used by both jobs to access the DB.

    Returns:
        A configured (but not started) AsyncIOScheduler.
    """
    scheduler = AsyncIOScheduler(timezone=settings.scheduler_timezone)

    scheduler.add_job(
        run_price_cache_refresh,
        trigger="cron",
        hour=7,
        minute=0,
        args=[session_factory],
        id="refresh_price_cache",
        replace_existing=True,
    )

    scheduler.add_job(
        run_fx_rate_refresh,
        trigger="cron",
        hour=7,
        minute=5,
        args=[session_factory],
        id="refresh_fx_rates",
        replace_existing=True,
    )

    scheduler.add_job(
        run_monthly_report,
        trigger="cron",
        day=1,
        hour=8,
        minute=0,
        args=[session_factory],
        id="send_monthly_report",
        replace_existing=True,
    )

    return scheduler
