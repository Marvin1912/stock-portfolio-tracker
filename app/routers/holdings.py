"""CRUD routes for portfolio holdings."""

from __future__ import annotations

import datetime

import plotly.graph_objects as go
import plotly.io as pio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_async_session
from app.models.holding import Holding
from app.models.stock import Stock
from app.schemas.holdings import HoldingCreate, HoldingResponse, HoldingUpdate, PortfolioSummary
from app.services.portfolio_service import PortfolioService

router = APIRouter(prefix="/holdings", tags=["holdings"])

_DB = Depends(get_async_session)


def _add_year_boundaries(fig: go.Figure, dates: list[str]) -> None:
    """Draw a faint dotted vertical line at each Jan-1 within the series span.

    *dates* are ISO date strings (``YYYY-MM-DD``) sorted ascending, as sent to
    Plotly's date x-axis.  For every calendar year that starts inside the data
    range a separator is added, labelled with the year at the top of the plot,
    so the time-series charts read clearly across year boundaries.
    """
    if len(dates) < 2:
        return
    first = datetime.date.fromisoformat(dates[0])
    last = datetime.date.fromisoformat(dates[-1])
    for year in range(first.year + 1, last.year + 1):
        boundary = datetime.date(year, 1, 1).isoformat()
        fig.add_vline(x=boundary, line={"color": "#6e7681", "width": 1, "dash": "dot"})
        fig.add_annotation(
            x=boundary,
            yref="paper",
            y=1,
            text=str(year),
            showarrow=False,
            font={"color": "#8b949e", "size": 11},
            yanchor="bottom",
            xanchor="left",
            xshift=3,
        )


async def _get_or_404(holding_id: int, db: AsyncSession) -> Holding:
    result = await db.get(Holding, holding_id, options=[selectinload(Holding.stock)])
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Holding not found")
    return result


@router.get("/chart/performance")
async def get_performance_chart(
    db: AsyncSession = _DB,
    year: int | None = Query(None),
) -> Response:
    """Return a Plotly line chart of total portfolio value since the first transaction."""
    service = PortfolioService()
    if year is not None:
        since: datetime.date | None = datetime.date(year, 1, 1)
        until = min(datetime.date(year, 12, 31), datetime.date.today())
    else:
        since = await service.earliest_transaction_date(db)
        until = datetime.date.today()
    performance = await service.get_performance_history(db, since=since)
    performance = [(d, v) for d, v in performance if d <= until]

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
    _add_year_boundaries(fig, dates)
    fig.update_layout(
        margin={"t": 20, "b": 40, "l": 60, "r": 20},
        xaxis={"showgrid": False},
        yaxis={"tickformat": ",.0f", "showgrid": True, "gridcolor": "#eee"},
        hovermode="x unified",
        plot_bgcolor="#fff",
        paper_bgcolor="#fff",
    )
    return Response(
        content=pio.to_json(fig),
        media_type="application/json",
        headers={"Cache-Control": "max-age=300, private"},
    )


@router.get("/chart/gain-loss")
async def get_gain_loss_chart(
    db: AsyncSession = _DB,
    year: int | None = Query(None),
) -> Response:
    """Return a Plotly line chart of Total P/L since the first transaction."""
    history = await PortfolioService().get_gain_loss_history(db)
    if year is not None:
        since_d = datetime.date(year, 1, 1)
        until_d = min(datetime.date(year, 12, 31), datetime.date.today())
        history = [(d, v) for d, v in history if since_d <= d <= until_d]

    if not history:
        return JSONResponse(content={})

    dates = [str(d) for d, _ in history]
    values = [float(v) for _, v in history]

    fig = go.Figure(
        go.Scatter(
            x=dates,
            y=values,
            mode="lines",
            line={"color": "#0066cc", "width": 2},
            hovertemplate="%{x}<br>P/L: %{y:,.2f}<extra></extra>",
        )
    )
    # Break-even baseline so the crossover between gain and loss is obvious.
    fig.add_hline(y=0, line={"color": "#888", "width": 1, "dash": "dash"})
    _add_year_boundaries(fig, dates)
    fig.update_layout(
        margin={"t": 20, "b": 40, "l": 60, "r": 20},
        xaxis={"showgrid": False},
        yaxis={"tickformat": ",.0f", "showgrid": True, "gridcolor": "#eee"},
        hovermode="x unified",
        plot_bgcolor="#fff",
        paper_bgcolor="#fff",
    )
    return Response(
        content=pio.to_json(fig),
        media_type="application/json",
        headers={"Cache-Control": "max-age=300, private"},
    )


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
    return JSONResponse(
        content=fig.to_dict(),
        headers={"Cache-Control": "max-age=300, private"},
    )


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
    rows = await db.execute(select(Holding).options(selectinload(Holding.stock)))
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
