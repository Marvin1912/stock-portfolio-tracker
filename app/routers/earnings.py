"""HTML route for the earnings / annual P&L page."""

from __future__ import annotations

import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.services.portfolio_service import PortfolioService

router = APIRouter(tags=["earnings-ui"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)


@router.get("/earnings", response_class=HTMLResponse)
async def earnings_page(
    request: Request,
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Render the earnings page with Gain/Loss and annual earnings charts."""
    service = PortfolioService()
    current_year = datetime.date.today().year
    earliest = await service.earliest_transaction_date(db)
    earliest_year = earliest.year if earliest else current_year
    chart_years = list(range(current_year, earliest_year - 1, -1))

    return templates.TemplateResponse(
        request=request,
        name="earnings.html",
        context={"chart_years": chart_years},
    )
