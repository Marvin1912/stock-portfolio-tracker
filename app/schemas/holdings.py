"""Pydantic schemas for the holdings endpoints."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class HoldingBase(BaseModel):
    quantity: Decimal = Field(..., gt=0, decimal_places=8)


class HoldingCreate(HoldingBase):
    ticker: str = Field(..., min_length=1, max_length=20)


class HoldingUpdate(BaseModel):
    quantity: Decimal = Field(..., gt=0, decimal_places=8)


class HoldingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    name: str
    quantity: Decimal


class HoldingSummaryItem(BaseModel):
    id: int
    ticker: str
    name: str
    quantity: Decimal
    current_price: Decimal | None
    current_value: Decimal | None


class PortfolioSummary(BaseModel):
    holdings: list[HoldingSummaryItem]
    total_value: Decimal | None
