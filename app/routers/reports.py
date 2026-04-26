"""HTML routes for the monthly report history and detail pages."""

from __future__ import annotations

import calendar
import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.services.report_service import ReportService

router = APIRouter(prefix="/reports", tags=["reports-ui"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)


@router.get("", response_class=HTMLResponse)
async def reports_history(
    request: Request,
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Render the monthly report history page."""
    available = await ReportService().get_available_months(db)
    months = [
        {
            "year": y,
            "month": m,
            "label": datetime.date(y, m, 1).strftime("%B %Y"),
            "period_start": datetime.date(y, m, 1),
            "period_end": datetime.date(y, m, calendar.monthrange(y, m)[1]),
        }
        for y, m in available
    ]
    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={"months": months},
    )


@router.get("/{year}/{month}", response_class=HTMLResponse)
async def report_detail(
    year: int,
    month: int,
    request: Request,
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Render the detail page for a specific month's report."""
    if not (1 <= month <= 12):
        raise HTTPException(status_code=404, detail="Invalid month")
    if datetime.date(year, month, 1) >= datetime.date.today().replace(day=1):
        raise HTTPException(status_code=404, detail="Month not yet complete")

    report = await ReportService().generate_report_for_month(db, year, month)
    month_label = datetime.date(year, month, 1).strftime("%B %Y")
    return templates.TemplateResponse(
        request=request,
        name="report_detail.html",
        context={
            "report": report,
            "year": year,
            "month": month,
            "month_label": month_label,
        },
    )
