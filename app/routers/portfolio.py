"""HTML routes for the portfolio overview page."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_async_session
from app.models.holding import Holding
from app.services.fx_service import to_eur

router = APIRouter(tags=["portfolio-ui"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)


@dataclass
class HoldingRow:
    id: int
    ticker: str
    name: str
    quantity: Decimal
    current_value: Decimal | None


@router.get("/", response_class=HTMLResponse)
async def portfolio_overview(
    request: Request,
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Render the portfolio overview page."""
    rows_result = await db.execute(select(Holding).options(selectinload(Holding.stock)))
    holdings = rows_result.scalars().all()

    holding_rows: list[HoldingRow] = []
    total_value: Decimal | None = None

    for h in holdings:
        stock = h.stock
        current_value: Decimal | None = None
        if stock.current_price is not None:
            eur_price = to_eur(stock.current_price, stock.currency)
            current_value = h.quantity * eur_price
            total_value = (total_value or Decimal("0")) + current_value

        holding_rows.append(
            HoldingRow(
                id=h.id,
                ticker=stock.ticker,
                name=stock.name,
                quantity=h.quantity,
                current_value=current_value,
            )
        )

    return templates.TemplateResponse(
        request=request,
        name="portfolio.html",
        context={
            "holdings": holding_rows,
            "total_value": total_value,
        },
    )
