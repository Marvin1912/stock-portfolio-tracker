"""Pydantic schemas for the dashboard REST API endpoints."""

from __future__ import annotations

import datetime
from decimal import Decimal

from pydantic import BaseModel


class DashboardPortfolioValue(BaseModel):
    total_value: Decimal | None
    currency: str
    as_of: datetime.datetime


class DashboardBitcoinValue(BaseModel):
    ticker: str
    name: str
    quantity: Decimal
    current_price: Decimal | None
    current_value: Decimal | None
    percentage_of_portfolio: Decimal | None
