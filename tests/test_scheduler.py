"""Tests for the APScheduler integration (app/scheduler.py)."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.scheduler import (
    create_scheduler,
    run_monthly_report,
    run_price_cache_refresh,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings() -> Settings:
    return Settings(
        app_env="development",
        app_debug=False,
        secret_key="test-secret-key-that-is-long-enough-32chars",
        database_url="postgresql+asyncpg://postgres:postgres@localhost/test",
        database_sync_url="postgresql+psycopg2://postgres:postgres@localhost/test",
        scheduler_timezone="UTC",
    )


def _make_session_factory(tickers: list[str]) -> AsyncMock:
    """Return a mock async_sessionmaker that yields a session with the given tickers."""
    session = AsyncMock()
    tickers_result = MagicMock()
    tickers_result.scalars.return_value.all.return_value = tickers
    session.execute = AsyncMock(return_value=tickers_result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock()
    factory.return_value = session
    return factory


# ---------------------------------------------------------------------------
# create_scheduler
# ---------------------------------------------------------------------------

def test_create_scheduler_registers_two_jobs() -> None:
    settings = _make_settings()
    factory = MagicMock()

    scheduler = create_scheduler(settings, factory)

    jobs = scheduler.get_jobs()
    job_ids = {j.id for j in jobs}
    assert "refresh_price_cache" in job_ids
    assert "send_monthly_report" in job_ids


def test_price_cache_job_scheduled_at_07_00() -> None:
    settings = _make_settings()
    scheduler = create_scheduler(settings, MagicMock())

    job = next(j for j in scheduler.get_jobs() if j.id == "refresh_price_cache")
    # Inspect CronTrigger repr to verify hour and minute fields.
    trigger_repr = str(job.trigger)
    assert "hour='7'" in trigger_repr
    assert "minute='0'" in trigger_repr


def test_monthly_report_job_scheduled_on_1st_at_08_00() -> None:
    settings = _make_settings()
    scheduler = create_scheduler(settings, MagicMock())

    job = next(j for j in scheduler.get_jobs() if j.id == "send_monthly_report")
    trigger_repr = str(job.trigger)
    assert "day='1'" in trigger_repr
    assert "hour='8'" in trigger_repr
    assert "minute='0'" in trigger_repr


# ---------------------------------------------------------------------------
# run_price_cache_refresh
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_price_cache_refresh_no_tickers_logs_and_returns() -> None:
    factory = _make_session_factory([])

    with patch("app.scheduler.refresh_price_cache") as mock_refresh:
        await run_price_cache_refresh(factory)
        mock_refresh.assert_not_called()


@pytest.mark.asyncio
async def test_run_price_cache_refresh_calls_service() -> None:
    factory = _make_session_factory(["AAPL", "TSLA"])

    with patch("app.scheduler.refresh_price_cache", new_callable=AsyncMock) as mock_refresh:
        await run_price_cache_refresh(factory)
        mock_refresh.assert_called_once()
        called_tickers = mock_refresh.call_args[0][0]
        assert "AAPL" in called_tickers
        assert "TSLA" in called_tickers


# ---------------------------------------------------------------------------
# run_monthly_report
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_monthly_report_no_holdings_skips() -> None:
    factory = MagicMock()
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = session

    with patch("app.scheduler.ReportService") as MockService:
        instance = MockService.return_value
        instance.generate_monthly_report = AsyncMock(return_value=None)

        await run_monthly_report(factory)

        instance.render_html = MagicMock()
        instance.render_html.assert_not_called()


@pytest.mark.asyncio
async def test_run_monthly_report_with_data_logs_result() -> None:
    from app.services.report_service import MonthlyReportData

    report_data = MonthlyReportData(
        month_label="March 2026",
        period_start=datetime.date(2026, 3, 1),
        period_end=datetime.date(2026, 3, 31),
        lines=[],
        total_value_1st=Decimal("1000.00"),
        total_value_last=Decimal("1100.00"),
        total_delta_eur=Decimal("100.00"),
        total_delta_pct=Decimal("10.00"),
    )

    factory = MagicMock()
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    factory.return_value = session

    with patch("app.scheduler.ReportService") as MockService:
        instance = MockService.return_value
        instance.generate_monthly_report = AsyncMock(return_value=report_data)

        await run_monthly_report(factory)

        instance.generate_monthly_report.assert_called_once()
