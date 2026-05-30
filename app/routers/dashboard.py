"""Lightweight JSON endpoints for the Raspberry Pi touch screen dashboard."""

from __future__ import annotations

import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.schemas.dashboard import DashboardBitcoinValue, DashboardPortfolioValue
from app.services.portfolio_service import PortfolioService

router = APIRouter(prefix="/api/v1/portfolio", tags=["dashboard"])

_DB = Depends(get_async_session)


@router.get("/value", response_model=DashboardPortfolioValue)
async def get_portfolio_value(db: AsyncSession = _DB) -> DashboardPortfolioValue:
    """Return the current total portfolio value in EUR."""
    summary = await PortfolioService().get_summary(db)
    return DashboardPortfolioValue(
        total_value=summary.total_value,
        currency="EUR",
        as_of=datetime.datetime.now(datetime.timezone.utc),
    )


@router.get("/bitcoin", response_model=DashboardBitcoinValue)
async def get_bitcoin_value(db: AsyncSession = _DB) -> DashboardBitcoinValue:
    """Return the current BTC holding's price, value, and portfolio share."""
    summary = await PortfolioService().get_summary(db)
    btc = next(
        (
            h
            for h in summary.holdings
            if h.asset_type == "CRYPTO" and h.ticker.upper().startswith("BTC")
        ),
        None,
    )
    if btc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No Bitcoin holding found",
        )

    percentage: Decimal | None = None
    if btc.current_value is not None and summary.total_value:
        percentage = (btc.current_value / summary.total_value * 100).quantize(
            Decimal("0.01")
        )

    return DashboardBitcoinValue(
        ticker=btc.ticker,
        name=btc.name,
        quantity=btc.quantity,
        current_price=btc.current_price,
        current_value=btc.current_value,
        percentage_of_portfolio=percentage,
    )
