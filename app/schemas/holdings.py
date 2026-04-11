"""Pydantic schemas for the holdings endpoints."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class HoldingBase(BaseModel):
    quantity: Decimal = Field(..., gt=0, decimal_places=8)


class HoldingCreate(HoldingBase):
    wkn: str = Field(..., min_length=6, max_length=6)

    @field_validator("wkn")
    @classmethod
    def wkn_alphanumeric(cls, v: str) -> str:
        if not v.isalnum():
            raise ValueError("WKN must be exactly 6 alphanumeric characters")
        return v.upper()


class HoldingUpdate(BaseModel):
    quantity: Decimal = Field(..., gt=0, decimal_places=8)


class HoldingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    wkn: str
    name: str
    quantity: Decimal


class HoldingSummaryItem(BaseModel):
    id: int
    wkn: str
    name: str
    quantity: Decimal
    current_price: Decimal | None
    current_value: Decimal | None


class PortfolioSummary(BaseModel):
    holdings: list[HoldingSummaryItem]
    total_value: Decimal | None
