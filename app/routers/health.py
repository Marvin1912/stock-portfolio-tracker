"""Health check router.

Exposes a single ``GET /health`` endpoint that returns the application
status and version. No database call is made so the endpoint stays
available even when the database is temporarily unreachable.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

__all__ = ["router"]

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Response schema for the health check endpoint."""

    status: str
    version: str
    environment: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Application health check",
    description="Returns HTTP 200 with application status when the service is running.",
)
async def health_check() -> HealthResponse:
    """Return a simple health status payload.

    This endpoint is intentionally lightweight — it performs no I/O so
    it can be used as a liveness probe without adding database load.
    """
    from app.main import app  # local import avoids circular dependency

    return HealthResponse(
        status="ok",
        version=app.version,
        environment=app.state.settings.app_env,
    )
