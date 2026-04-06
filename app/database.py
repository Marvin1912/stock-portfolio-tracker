"""Async SQLAlchemy 2.x engine, session factory, and base model class."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings, get_settings

__all__ = ["Base", "get_async_session", "build_engine", "build_session_factory"]


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


def build_engine(settings: Settings | None = None) -> AsyncEngine:
    """Create and return a configured async SQLAlchemy engine.

    Args:
        settings: Optional Settings instance; defaults to ``get_settings()``.

    Returns:
        An ``AsyncEngine`` connected to the configured PostgreSQL database.
    """
    cfg = settings or get_settings()
    return create_async_engine(
        cfg.database_url,
        echo=cfg.app_debug,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )


def build_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create and return a bound session factory.

    Args:
        engine: The async engine to bind the factory to.

    Returns:
        An ``async_sessionmaker`` that produces ``AsyncSession`` instances.
    """
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
        autocommit=False,
    )


# Module-level singletons — initialised in app lifespan
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(settings: Settings | None = None) -> None:
    """Initialise the module-level engine and session factory.

    Call this once during application startup (inside the FastAPI lifespan).

    Args:
        settings: Optional Settings instance; defaults to ``get_settings()``.
    """
    global _engine, _session_factory
    _engine = build_engine(settings)
    _session_factory = build_session_factory(_engine)


async def close_db() -> None:
    """Dispose the engine and release all connections.

    Call this during application shutdown (inside the FastAPI lifespan).
    """
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a database session per request.

    Usage::

        @router.get("/items")
        async def list_items(db: AsyncSession = Depends(get_async_session)):
            ...

    Raises:
        RuntimeError: If ``init_db`` has not been called before first use.
    """
    if _session_factory is None:
        raise RuntimeError("Database not initialised — call init_db() during app startup.")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
