"""Persists Portfolio Performance XML transactions to the database."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import ASSET_TYPE_STOCK, Stock
from app.models.transaction import TX_SOURCE_XML, Transaction
from app.services.portfolio_performance_importer import (
    ParsedTransaction,
    ParseResult,
    SecurityInfo,
)

# Portfolio Performance type code → our internal Transaction.type.
# Anything outside this map is ignored (e.g. DEPOSIT, REMOVAL, INTEREST).
_PP_TYPE_MAP: dict[str, str] = {
    "BUY": "BUY",
    "SELL": "SELL",
    "DIVIDENDS": "DIVIDEND",
    "FEES": "FEE",
    "TAXES": "TAX",
    "TRANSFER_IN": "TRANSFER_IN",
    "TRANSFER_OUT": "TRANSFER_OUT",
}


@dataclass(slots=True)
class ImportSummary:
    created: int = 0
    skipped_existing: int = 0
    skipped_unsupported: int = 0
    affected_stock_ids: set[int] | None = None

    def __post_init__(self) -> None:
        if self.affected_stock_ids is None:
            self.affected_stock_ids = set()


class TransactionImportService:
    """Upsert Stock rows + insert Transaction rows from a parsed XML result."""

    async def import_xml_result(
        self,
        result: ParseResult,
        db: AsyncSession,
    ) -> ImportSummary:
        summary = ImportSummary()

        for tx in result.transactions:
            if not await self._persist(tx, db, summary):
                continue

        await db.flush()
        return summary

    async def _persist(
        self,
        tx: ParsedTransaction,
        db: AsyncSession,
        summary: ImportSummary,
    ) -> bool:
        mapped_type = _PP_TYPE_MAP.get(tx.type)
        if mapped_type is None:
            summary.skipped_unsupported += 1
            return False

        # For BUY/SELL keep only the portfolio side — the paired account leg
        # is the cash counter-entry without share data and would otherwise be
        # double-counted as a position change.
        if mapped_type in {"BUY", "SELL"} and tx.kind != "portfolio":
            summary.skipped_unsupported += 1
            return False

        if not tx.uuid:
            summary.skipped_unsupported += 1
            return False

        existing = await db.execute(
            select(Transaction.id).where(Transaction.external_uuid == tx.uuid)
        )
        if existing.scalar_one_or_none() is not None:
            summary.skipped_existing += 1
            return False

        stock_id: int | None = None
        if tx.security is not None:
            stock = await self._upsert_stock(tx.security, db)
            if stock is not None:
                stock_id = stock.id

        # BUY/SELL/TRANSFER without a stock cannot be meaningfully tracked.
        if mapped_type in {"BUY", "SELL", "TRANSFER_IN", "TRANSFER_OUT"} and stock_id is None:
            summary.skipped_unsupported += 1
            return False

        db.add(
            Transaction(
                external_uuid=tx.uuid,
                stock_id=stock_id,
                date=tx.date,
                type=mapped_type,
                shares=tx.shares,
                amount=tx.amount,
                currency=tx.currency or "EUR",
                fee=tx.fees,
                tax=tx.taxes,
                note=tx.note,
                source=TX_SOURCE_XML,
            )
        )
        summary.created += 1
        if stock_id is not None:
            assert summary.affected_stock_ids is not None
            summary.affected_stock_ids.add(stock_id)
        return True

    async def _upsert_stock(
        self,
        security: SecurityInfo,
        db: AsyncSession,
    ) -> Stock | None:
        ticker = (security.ticker or "").strip().upper()
        if not ticker:
            return None

        result = await db.execute(select(Stock).where(Stock.ticker == ticker))
        stock = result.scalar_one_or_none()
        if stock is not None:
            return stock

        stock = Stock(
            ticker=ticker,
            name=security.name or ticker,
            currency=(security.currency or "EUR").upper(),
            asset_type=ASSET_TYPE_STOCK,
        )
        db.add(stock)
        await db.flush()
        return stock
