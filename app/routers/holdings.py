"""CRUD routes for portfolio holdings."""

from __future__ import annotations

import plotly.graph_objects as go
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.holding import Holding
from app.models.stock import Stock
from app.schemas.holdings import HoldingCreate, HoldingResponse, HoldingUpdate, PortfolioSummary
from app.services.portfolio_service import PortfolioService

router = APIRouter(prefix="/holdings", tags=["holdings"])

_DB = Depends(get_async_session)


async def _get_or_404(holding_id: int, db: AsyncSession) -> Holding:
    result = await db.get(Holding, holding_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Holding not found")
    return result


@router.get("/chart/performance")
async def get_performance_chart(
    db: AsyncSession = _DB,
) -> JSONResponse:
    """Return a Plotly line chart of total portfolio value over the past year."""
    performance = await PortfolioService().get_performance_history(db)

    if not performance:
        return JSONResponse(content={})

    dates = [str(d) for d, _ in performance]
    values = [float(v) for _, v in performance]

    fig = go.Figure(
        go.Scatter(
            x=dates,
            y=values,
            mode="lines",
            line={"color": "#0066cc", "width": 2},
            hovertemplate="%{x}<br>Value: %{y:,.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        margin={"t": 20, "b": 40, "l": 60, "r": 20},
        xaxis={"showgrid": False},
        yaxis={"tickformat": ",.0f", "showgrid": True, "gridcolor": "#eee"},
        hovermode="x unified",
        plot_bgcolor="#fff",
        paper_bgcolor="#fff",
    )
    return JSONResponse(content=fig.to_dict())


@router.get("/chart/allocation")
async def get_allocation_chart(
    db: AsyncSession = _DB,
) -> JSONResponse:
    """Return a Plotly donut chart of portfolio allocation as JSON."""
    summary = await PortfolioService().get_summary(db)
    valued = [h for h in summary.holdings if h.current_value is not None]

    if not valued:
        return JSONResponse(content={})

    fig = go.Figure(
        go.Pie(
            labels=[h.ticker for h in valued],
            values=[float(h.current_value) for h in valued],  # type: ignore[arg-type]
            hole=0.5,
            customdata=[h.name for h in valued],
            hovertemplate=(
                "<b>%{label}</b> — %{customdata}<br>"
                "Value: %{value:,.2f}<br>"
                "%{percent}<extra></extra>"
            ),
        )
    )
    fig.update_layout(
        margin={"t": 20, "b": 20, "l": 20, "r": 20},
        showlegend=True,
    )
    return JSONResponse(content=fig.to_dict())


@router.get("/summary", response_model=PortfolioSummary)
async def get_holdings_summary(
    db: AsyncSession = _DB,
) -> PortfolioSummary:
    """Return current market value per holding and total portfolio value."""
    return await PortfolioService().get_summary(db)


@router.get("", response_model=list[HoldingResponse])
async def list_holdings(
    db: AsyncSession = _DB,
) -> list[HoldingResponse]:
    """Return all holdings with ticker, name, and quantity."""
    rows = await db.execute(select(Holding).join(Holding.stock))
    holdings = rows.scalars().all()
    return [
        HoldingResponse(
            id=h.id,
            ticker=h.stock.ticker,
            name=h.stock.name,
            quantity=h.quantity,
        )
        for h in holdings
    ]


@router.post("", response_model=HoldingResponse, status_code=status.HTTP_201_CREATED)
async def create_holding(
    payload: HoldingCreate,
    db: AsyncSession = _DB,
) -> HoldingResponse:
    """Add a new holding by ticker and quantity.

    If the ticker does not exist a 404 is returned.
    """
    result = await db.execute(
        select(Stock).where(Stock.ticker == payload.ticker.upper())
    )
    stock = result.scalar_one_or_none()
    if stock is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stock with ticker '{payload.ticker}' not found",
        )

    holding = Holding(stock_id=stock.id, quantity=payload.quantity)
    db.add(holding)
    await db.flush()
    await db.refresh(holding)

    return HoldingResponse(
        id=holding.id,
        ticker=stock.ticker,
        name=stock.name,
        quantity=holding.quantity,
    )


@router.put("/{holding_id}", response_model=HoldingResponse)
async def update_holding(
    holding_id: int,
    payload: HoldingUpdate,
    db: AsyncSession = _DB,
) -> HoldingResponse:
    """Update the quantity of an existing holding."""
    holding = await _get_or_404(holding_id, db)
    holding.quantity = payload.quantity
    await db.flush()
    await db.refresh(holding)

    return HoldingResponse(
        id=holding.id,
        ticker=holding.stock.ticker,
        name=holding.stock.name,
        quantity=holding.quantity,
    )


@router.delete("/{holding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_holding(
    holding_id: int,
    db: AsyncSession = _DB,
) -> None:
    """Remove a holding entirely."""
    holding = await _get_or_404(holding_id, db)
    await db.delete(holding)
