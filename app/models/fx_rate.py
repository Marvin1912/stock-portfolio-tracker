"""ORM model for the FxRate entity."""

from __future__ import annotations

import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class FxRate(Base):
    """Stores the most recently fetched EUR/{CURRENCY} exchange rate.

    ``rate`` is the value of ``EUR{CURRENCY}=X`` — i.e. units of
    *currency* per 1 EUR (e.g. USD=1.10 means 1 EUR = 1.10 USD).
    """

    __tablename__ = "fx_rate"
    __table_args__ = ({"schema": "finance"},)

    currency: Mapped[str] = mapped_column(String(10), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=8), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
