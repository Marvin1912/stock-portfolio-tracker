"""Routes for individual stock detail pages."""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import plotly.graph_objects as go
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.holding import Holding
from app.models.price_cache import PriceCache
from app.models.stock import Stock

router = APIRouter(tags=["stocks"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)


@dataclass
class StockDetail:
    wkn: str
    name: str
    currency: str
    current_price: Decimal | None
    quantity: Decimal | None
    current_value: Decimal | None


async def _get_stock_or_404(wkn: str, db: AsyncSession) -> Stock:
    result = await db.execute(select(Stock).where(Stock.wkn == wkn.upper()))
    stock = result.scalar_one_or_none()
    if stock is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock '{wkn}' not found",
        )
    return stock


@router.get("/stocks/{wkn}", response_class=HTMLResponse)
async def stock_detail(
    wkn: str,
    request: Request,
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Render the stock detail page for the given WKN."""
    stock = await _get_stock_or_404(wkn, db)

    holding_result = await db.execute(select(Holding).where(Holding.stock_id == stock.id))
    holding = holding_result.scalar_one_or_none()

    quantity: Decimal | None = None
    current_value: Decimal | None = None
    if holding is not None:
        quantity = holding.quantity
        if stock.current_price is not None:
            current_value = quantity * stock.current_price

    detail = StockDetail(
        wkn=stock.wkn,
        name=stock.name,
        currency=stock.currency,
        current_price=stock.current_price,
        quantity=quantity,
        current_value=current_value,
    )

    return templates.TemplateResponse(
        request=request,
        name="stock_detail.html",
        context={"stock": detail},
    )


@router.get("/api/v1/stocks/{wkn}/chart/price-history")
async def get_price_history_chart(
    wkn: str,
    db: AsyncSession = _DB,
) -> JSONResponse:
    """Return a Plotly line chart of the stock's 1Y price history as JSON."""
    stock = await _get_stock_or_404(wkn, db)

    one_year_ago = datetime.date.today() - datetime.timedelta(days=365)
    price_rows = await db.execute(
        select(PriceCache.date, PriceCache.close_price)
        .where(
            PriceCache.ticker == stock.ticker,
            PriceCache.date >= one_year_ago,
        )
        .order_by(PriceCache.date)
    )
    prices = price_rows.all()

    if not prices:
        return JSONResponse(content={})

    dates = [str(row.date) for row in prices]
    values = [float(row.close_price) for row in prices]

    fig = go.Figure(
        go.Scatter(
            x=dates,
            y=values,
            mode="lines",
            line={"color": "#0066cc", "width": 2},
            hovertemplate="%{x}<br>Price: %{y:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        margin={"t": 20, "b": 40, "l": 60, "r": 20},
        xaxis={"showgrid": False},
        yaxis={"tickformat": ",.2f", "showgrid": True, "gridcolor": "#eee"},
        hovermode="x unified",
        plot_bgcolor="#fff",
        paper_bgcolor="#fff",
    )
    return JSONResponse(content=fig.to_dict())
