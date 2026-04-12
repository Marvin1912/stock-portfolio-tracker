"""Tests for the admin router (POST /admin/trigger-report)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


def _make_settings() -> Settings:
    return Settings(
        app_env="development",
        app_debug=False,
        secret_key="test-secret-key-that-is-long-enough-32chars",
        database_url="postgresql+asyncpg://postgres:postgres@localhost/test",
    )


def _mock_scheduler() -> MagicMock:
    sched = MagicMock()
    sched.start = MagicMock()
    sched.shutdown = MagicMock()
    return sched


@pytest.mark.asyncio
async def test_trigger_report_returns_ok() -> None:
    app = create_app(settings=_make_settings())
    mock_session_factory = MagicMock()

    with (
        patch("app.main.init_db"),
        patch("app.main.build_engine"),
        patch("app.main.build_session_factory", return_value=mock_session_factory),
        patch("app.main.close_db", new_callable=AsyncMock),
        patch("app.scheduler.create_scheduler", return_value=_mock_scheduler()),
        patch("app.scheduler.run_price_cache_refresh", new_callable=AsyncMock),
        patch("app.database._session_factory", mock_session_factory),
        patch("app.scheduler.run_monthly_report", new_callable=AsyncMock) as mock_run,
    ):
        transport = ASGITransport(app=app)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/admin/trigger-report")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    mock_run.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_report_db_not_initialised_returns_503() -> None:
    app = create_app(settings=_make_settings())

    with (
        patch("app.main.init_db"),
        patch("app.main.build_engine"),
        patch("app.main.build_session_factory"),
        patch("app.main.close_db", new_callable=AsyncMock),
        patch("app.scheduler.create_scheduler", return_value=_mock_scheduler()),
        patch("app.scheduler.run_price_cache_refresh", new_callable=AsyncMock),
        patch("app.database._session_factory", None),
    ):
        transport = ASGITransport(app=app)  # type: ignore[arg-type]
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/admin/trigger-report")

    assert response.status_code == 503
