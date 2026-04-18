"""Admin endpoints for manual job triggering."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/trigger-report", status_code=200)
async def trigger_report() -> dict[str, str]:
    """Manually trigger monthly report generation for testing.

    Runs the same job that executes on the 1st of each month.
    """
    from app.database import _session_factory
    from app.scheduler import run_monthly_report

    if _session_factory is None:
        raise HTTPException(status_code=503, detail="Database not initialised.")

    await run_monthly_report(_session_factory)
    return {"status": "ok", "message": "Monthly report job triggered."}


@router.post("/refresh-prices", status_code=200)
async def refresh_prices(response: Response) -> dict[str, str]:
    """Manually trigger price cache and FX rate refresh.

    Runs the same jobs that execute daily at 07:00 / 07:05. When called via
    HTMX, responds with ``HX-Refresh: true`` so the client reloads the page
    to show the updated values.
    """
    from app.database import _session_factory
    from app.scheduler import run_fx_rate_refresh, run_price_cache_refresh

    if _session_factory is None:
        raise HTTPException(status_code=503, detail="Database not initialised.")

    await run_price_cache_refresh(_session_factory)
    await run_fx_rate_refresh(_session_factory)

    response.headers["HX-Refresh"] = "true"
    return {"status": "ok", "message": "Price cache and FX rates refreshed."}
