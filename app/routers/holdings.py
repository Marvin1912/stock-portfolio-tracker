"""CRUD routes for portfolio holdings."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.holding import Holding
from app.models.stock import Stock
from app.schemas.holdings import HoldingCreate, HoldingResponse, HoldingUpdate

router = APIRouter(prefix="/holdings", tags=["holdings"])

_DB = Depends(get_async_session)


async def _get_or_404(holding_id: int, db: AsyncSession) -> Holding:
    result = await db.get(Holding, holding_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Holding not found")
    return result


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
