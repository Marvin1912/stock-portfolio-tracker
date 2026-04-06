"""FastAPI application entrypoint and lifespan management."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.database import close_db, init_db
from app.routers import health

__all__ = ["app", "create_app"]

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Manage startup and shutdown of shared resources.

    Startup: initialise the database engine.
    Shutdown: dispose all database connections.
    """
    settings: Settings = application.state.settings
    logger.info("Starting up — env=%s", settings.app_env)

    init_db(settings)
    logger.info("Database engine initialised.")

    yield

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
    # Future routers (uncomment as implemented):
    # app.include_router(auth.router,       prefix="/api/v1/auth",       tags=["auth"])
    # app.include_router(portfolios.router, prefix="/api/v1/portfolios", tags=["portfolios"])
    # app.include_router(trades.router,     prefix="/api/v1/trades",     tags=["trades"])
    # app.include_router(reports.router,    prefix="/api/v1/reports",    tags=["reports"])
    # app.include_router(pdf_import.router, prefix="/api/v1/imports",    tags=["pdf-import"])

    return app


# Module-level instance used by uvicorn / gunicorn
app: FastAPI = create_app()


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
