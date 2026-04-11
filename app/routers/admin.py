"""Admin endpoints for manual job triggering."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

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
