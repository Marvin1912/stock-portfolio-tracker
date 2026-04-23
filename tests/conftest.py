"""Shared pytest fixtures for the test suite.

All tests run against a mocked database session — no real PostgreSQL is
required.  The FastAPI lifespan (which normally initialises the DB engine
and APScheduler) is bypassed: ``ASGITransport`` does not fire lifespan
events, and the ``get_async_session`` dependency is overridden with a
``MagicMock`` that exposes the async methods real handlers call.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.database import get_async_session
from app.main import create_app


def _make_test_settings() -> Settings:
    return Settings(
        app_env="development",
        app_debug=False,
        secret_key="test-secret-key-that-is-long-enough-32chars",
        database_url="postgresql+asyncpg://mock/mock",
    )


def make_mock_session() -> MagicMock:
    """Return a MagicMock shaped like an ``AsyncSession`` with no-op async methods."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


@pytest.fixture
def mock_session() -> MagicMock:
    """A fresh mocked ``AsyncSession`` — customise per-test as needed."""
    return make_mock_session()


@pytest_asyncio.fixture
async def client(mock_session: MagicMock) -> AsyncIterator[AsyncClient]:
    """An ``AsyncClient`` against a FastAPI app backed by ``mock_session``.

    The app's lifespan is intentionally not triggered (ASGITransport skips
    lifespan events), so no real database engine or scheduler is created.
    """
    app = create_app(settings=_make_test_settings())

    async def _override() -> MagicMock:
        return mock_session

    app.dependency_overrides[get_async_session] = _override

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
