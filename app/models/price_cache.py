"""ORM model for the PriceCache entity."""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import Date, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PriceCache(Base):
    """Stores daily closing prices fetched from yfinance."""

    __tablename__ = "price_cache"
    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_price_cache_ticker_date"),
        {"schema": "costs"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    close_price: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=4), nullable=False
    )
