"""Shared pytest fixtures for the test suite.

The fixtures here create an in-process FastAPI test client backed by a
real (but test-scoped) database session.  When DATABASE_URL is not set
in the test environment, the tests that require a database are skipped
automatically.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


@pytest.fixture(scope="session")
def test_settings() -> Settings:
    """Return a Settings instance suitable for testing.

    Override DATABASE_URL / DATABASE_SYNC_URL via environment variables
    in CI to point at a real test database.
    """
    return Settings(  # type: ignore[call-arg]
        app_env="development",
        app_debug=True,
        secret_key="test-secret-key-that-is-long-enough-32chars",
        database_url=os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql+asyncpg://postgres:postgres@localhost:5432/portfolio_test",
        ),
        database_sync_url=os.environ.get(
            "TEST_DATABASE_SYNC_URL",
            "postgresql+psycopg2://postgres:postgres@localhost:5432/portfolio_test",
        ),
    )


@pytest_asyncio.fixture(scope="session")
async def client(test_settings: Settings) -> AsyncGenerator[AsyncClient, None]:
    """Yield an AsyncClient wired to the test FastAPI application.

    The app's lifespan (database init/close) is executed automatically.
    """
    application = create_app(settings=test_settings)
    transport = ASGITransport(app=application)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
