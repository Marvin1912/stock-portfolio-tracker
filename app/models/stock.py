"""ORM model for the Stock entity."""

from __future__ import annotations

from sqlalchemy import String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Stock(Base):
    """Represents a tradeable stock/security."""

    __tablename__ = "stock"
    __table_args__ = (
        UniqueConstraint("ticker", name="uq_stock_ticker"),
        {"schema": "costs"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)

    holdings: Mapped[list[Holding]] = relationship(
        "Holding", back_populates="stock", cascade="all, delete-orphan"
    )
