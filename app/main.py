"""FastAPI application entrypoint and lifespan management."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.database import build_engine, build_session_factory, close_db, init_db
from app.routers import (
    admin,
    health,
    holdings,
    htmx,
    import_pdf,
    import_xml,
    portfolio,
    reports,
    stocks,
)

__all__ = ["app", "create_app"]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of shared resources.

    Startup: initialise the database engine and APScheduler.
    Shutdown: stop the scheduler and dispose all database connections.
    """
    import asyncio

    from app.scheduler import (
        create_scheduler,
        run_fx_cache_warmup,
        run_fx_rate_refresh,
        run_price_cache_refresh,
    )

    settings: Settings = application.state.settings
    logger.info("Starting up — env=%s", settings.app_env)

    init_db(settings)
    logger.info("Database engine initialised.")

    # Build a dedicated engine/session factory for the scheduler so it is
    # independent of the per-request factory.
    _sched_engine = build_engine(settings)
    _sched_factory = build_session_factory(_sched_engine)

    scheduler = create_scheduler(settings, _sched_factory)
    scheduler.start()
    logger.info("Scheduler started (price cache: daily 07:00, monthly report: 1st 08:00).")

    # Warm the FX cache from the DB synchronously so conversions work
    # immediately — the yfinance refresh then runs in the background.
    try:
        await run_fx_cache_warmup(_sched_factory)
    except Exception:
        logger.exception("FX cache warm-up from DB failed.")

    # Run an initial cache warm-up in the background so it doesn't block startup.
    asyncio.create_task(run_price_cache_refresh(_sched_factory))
    asyncio.create_task(run_fx_rate_refresh(_sched_factory))

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
    app.include_router(import_xml.router)
    app.include_router(admin.router)
    app.include_router(reports.router)
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
