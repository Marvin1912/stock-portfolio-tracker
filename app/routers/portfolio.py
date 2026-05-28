"""HTML routes for the portfolio overview page."""

from __future__ import annotations

import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.price_cache import PriceCache
from app.services.portfolio_service import PortfolioService

router = APIRouter(tags=["portfolio-ui"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)


@router.get("/", response_class=HTMLResponse)
async def portfolio_overview(
    request: Request,
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Render the portfolio overview page."""
    service = PortfolioService()
    summary = await service.get_summary(db)

    last_refresh_result = await db.execute(select(func.max(PriceCache.date)))
    last_refresh: datetime.date | None = last_refresh_result.scalar()

    current_year = datetime.date.today().year
    earliest = await service.earliest_transaction_date(db)
    earliest_year = earliest.year if earliest else current_year
    chart_years = list(range(current_year, earliest_year - 1, -1))

    return templates.TemplateResponse(
        request=request,
        name="portfolio.html",
        context={
            "holdings": summary.holdings,
            "total_value": summary.total_value,
            "holdings_count": len(summary.holdings),
            "last_refresh": last_refresh,
            "chart_years": chart_years,
            "current_year": current_year,
        },
    )
