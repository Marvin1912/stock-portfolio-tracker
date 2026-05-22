"""ORM model for the Transaction entity."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.stock import Stock


TX_TYPE_BUY = "BUY"
TX_TYPE_SELL = "SELL"
TX_TYPE_DIVIDEND = "DIVIDEND"
TX_TYPE_FEE = "FEE"
TX_TYPE_TAX = "TAX"
TX_TYPE_TRANSFER_IN = "TRANSFER_IN"
TX_TYPE_TRANSFER_OUT = "TRANSFER_OUT"

TX_TYPES: tuple[str, ...] = (
    TX_TYPE_BUY,
    TX_TYPE_SELL,
    TX_TYPE_DIVIDEND,
    TX_TYPE_FEE,
    TX_TYPE_TAX,
    TX_TYPE_TRANSFER_IN,
    TX_TYPE_TRANSFER_OUT,
)

TX_SOURCE_XML = "XML"
TX_SOURCE_PDF = "PDF"
TX_SOURCE_MANUAL = "MANUAL"

TX_SOURCES: tuple[str, ...] = (TX_SOURCE_XML, TX_SOURCE_PDF, TX_SOURCE_MANUAL)


class Transaction(Base):
    """A buy/sell/dividend/fee/tax/transfer event from XML, PDF, or manual entry."""

    __tablename__ = "transaction"
    __table_args__ = (
        UniqueConstraint("external_uuid", name="uq_transaction_external_uuid"),
        Index("ix_transaction_stock_id_date", "stock_id", "date"),
        {"schema": "finance"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_uuid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stock_id: Mapped[int | None] = mapped_column(
        ForeignKey("finance.stock.id", ondelete="CASCADE"), nullable=True
    )
    date: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    type: Mapped[str] = mapped_column(String(16), nullable=False)
    shares: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=8), nullable=False, default=Decimal("0")
    )
    amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False, default=Decimal("0")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    fee: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False, default=Decimal("0")
    )
    tax: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), nullable=False, default=Decimal("0")
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    stock: Mapped[Stock | None] = relationship(
        "Stock", back_populates="transactions"
    )
