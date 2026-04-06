"""ORM model for the Holding entity."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Numeric
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.stock import Stock


class Holding(Base):
    """Represents the current holding of a stock in the portfolio."""

    __tablename__ = "holding"
    __table_args__ = {"schema": "costs"}

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(
        ForeignKey("costs.stock.id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=8), nullable=False
    )

    stock: Mapped[Stock] = relationship(
        "Stock", back_populates="holdings"
    )
