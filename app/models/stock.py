"""ORM model for the Stock entity."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.holding import Holding


class Stock(Base):
    """Represents a tradeable stock/security."""

    __tablename__ = "stock"
    __table_args__ = (
        UniqueConstraint("ticker", name="uq_stock_ticker"),
        {"schema": "finance"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    current_price: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=18, scale=4), nullable=True
    )

    holdings: Mapped[list[Holding]] = relationship(
        "Holding", back_populates="stock", cascade="all, delete-orphan"
    )
