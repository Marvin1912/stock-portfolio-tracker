"""FastAPI application entrypoint and lifespan management."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.database import build_engine, build_session_factory, close_db, init_db
from app.routers import health, holdings, htmx, import_pdf, portfolio, stocks

__all__ = ["app", "create_app"]

logger = logging.getLogger(__name__)


async def _refresh_price_cache_job(session_factory: object) -> None:
    """Scheduled job: refresh price cache for all tracked tickers."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models.stock import Stock
    from app.services.price_service import refresh_price_cache

    factory: async_sessionmaker[AsyncSession] = session_factory  # type: ignore[assignment]
    async with factory() as db:
        tickers_result = await db.execute(select(Stock.ticker))
        tickers = list(tickers_result.scalars().all())

    if not tickers:
        logger.info("No tickers to refresh.")
        return

    factory2: async_sessionmaker[AsyncSession] = session_factory  # type: ignore[assignment]
    async with factory2() as db:
        await refresh_price_cache(tickers, db)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of shared resources.

    Startup: initialise the database engine and price-cache scheduler.
    Shutdown: dispose all database connections.
    """
    settings: Settings = application.state.settings
    logger.info("Starting up — env=%s", settings.app_env)

    init_db(settings)
    logger.info("Database engine initialised.")

    # Build a dedicated session factory for the scheduler so it is
    # independent of the per-request factory.
    _sched_engine = build_engine(settings)
    _sched_factory = build_session_factory(_sched_engine)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _refresh_price_cache_job,
        trigger="cron",
        hour=6,
        minute=0,
        args=[_sched_factory],
        id="refresh_price_cache",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Price-cache scheduler started (daily at 06:00).")

    # Run an initial cache warm-up in the background so it doesn't block startup.
    import asyncio
    asyncio.create_task(_refresh_price_cache_job(_sched_factory))

    yield

    scheduler.shutdown(wait=False)
    await _sched_engine.dispose()
    await close_db()
    logger.info("Database connections closed.")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory — creates and configures the FastAPI instance.

    Args:
        settings: Optional pre-built Settings; useful in tests to inject
                  a custom configuration without touching the environment.

    Returns:
        A fully configured ``FastAPI`` application.
    """
    cfg = settings or get_settings()

    app = FastAPI(
        title="Stock Portfolio Tracker",
        description=(
            "Track your stock portfolio, import broker PDFs, "
            "and receive automated monthly reports."
        ),
        version="0.1.0",
        debug=cfg.app_debug,
        docs_url="/docs" if not cfg.is_production else None,
        redoc_url="/redoc" if not cfg.is_production else None,
        lifespan=lifespan,
    )

    # Store settings on app.state so lifespan and routes can access them.
    app.state.settings = cfg

    # ------------------------------------------------------------------
    # Middleware
    # ------------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in cfg.allowed_hosts],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Routers
    # ------------------------------------------------------------------
    app.include_router(health.router)
    app.include_router(portfolio.router)
    app.include_router(stocks.router)
    app.include_router(holdings.router, prefix="/api/v1")
    app.include_router(htmx.router)
    app.include_router(import_pdf.router)
    # Future routers (uncomment as implemented):
    # app.include_router(auth.router,       prefix="/api/v1/auth",       tags=["auth"])
    # app.include_router(portfolios.router, prefix="/api/v1/portfolios", tags=["portfolios"])
    # app.include_router(trades.router,     prefix="/api/v1/trades",     tags=["trades"])
    # app.include_router(reports.router,    prefix="/api/v1/reports",    tags=["reports"])
    # app.include_router(pdf_import.router, prefix="/api/v1/imports",    tags=["pdf-import"])

    return app


# Module-level instance used by uvicorn / gunicorn.
# Declared without assignment so that merely importing this module (e.g. in
# tests) does not trigger Settings validation.  The attribute is created on
# first access via __getattr__ below.
app: FastAPI


def __getattr__(name: str) -> FastAPI:
    if name == "app":
        import sys

        instance = create_app()
        sys.modules[__name__].app = instance  # type: ignore[attr-defined]
        return instance
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def run() -> None:
    """Entrypoint for the ``serve`` project script."""
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )


if __name__ == "__main__":
    run()
