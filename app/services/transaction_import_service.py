"""Persists Portfolio Performance XML transactions to the database."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import ASSET_TYPE_STOCK, Stock
from app.models.transaction import TX_SOURCE_XML, Transaction
from app.services.comdirect_ref import (
    build_comdirect_external_uuid,
    parse_comdirect_order_ref,
)
from app.services.portfolio_performance_importer import (
    ParsedTransaction,
    ParseResult,
    SecurityInfo,
)

logger = logging.getLogger(__name__)

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
        logger.info(
            "XML import done: created=%d, skipped_existing=%d, skipped_unsupported=%d",
            summary.created,
            summary.skipped_existing,
            summary.skipped_unsupported,
        )
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
            logger.debug(
                "Skipped XML transaction %s — unsupported PP type %r (kind=%s, security=%s)",
                tx.uuid or "?",
                tx.type,
                tx.kind,
                _describe_security(tx.security),
            )
            return False

        # For BUY/SELL keep only the portfolio side — the paired account leg
        # is the cash counter-entry without share data and would otherwise be
        # double-counted as a position change.
        if mapped_type in {"BUY", "SELL"} and tx.kind != "portfolio":
            summary.skipped_unsupported += 1
            logger.debug(
                "Skipped XML transaction %s — %s account-leg "
                "(paired with a portfolio entry, security=%s)",
                tx.uuid or "?",
                mapped_type,
                _describe_security(tx.security),
            )
            return False

        if not tx.uuid:
            summary.skipped_unsupported += 1
            logger.info(
                "Skipped XML transaction — missing UUID (type=%s, kind=%s, security=%s)",
                tx.type,
                tx.kind,
                _describe_security(tx.security),
            )
            return False

        # Prefer the cross-source comdirect key (derived from the Ordernummer in
        # the note) over PP's random per-export uuid, so an XML row dedupes
        # against a prior PDF import of the same trade. Non-comdirect rows keep
        # their PP uuid, which is stable across XML re-imports.
        ref = parse_comdirect_order_ref(tx.note)
        external_uuid = build_comdirect_external_uuid(ref) if ref else tx.uuid

        existing = await db.execute(
            select(Transaction.id).where(Transaction.external_uuid == external_uuid)
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
            if tx.security is None:
                reason = "no <security> element on transaction"
            elif not (tx.security.ticker or "").strip():
                reason = (
                    f"security has no ticker symbol "
                    f"(uuid={tx.security.uuid}, name={tx.security.name!r}, "
                    f"isin={tx.security.isin!r})"
                )
            else:
                reason = "stock upsert failed"
            logger.info(
                "Skipped XML transaction %s — %s without resolvable stock: %s",
                tx.uuid,
                mapped_type,
                reason,
            )
            return False

        db.add(
            Transaction(
                external_uuid=external_uuid,
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
            asset_type=(security.asset_type or ASSET_TYPE_STOCK).upper(),
        )
        db.add(stock)
        await db.flush()
        return stock


def _describe_security(security: SecurityInfo | None) -> str:
    if security is None:
        return "none"
    return (
        f"uuid={security.uuid}, name={security.name!r}, "
        f"isin={security.isin!r}, ticker={security.ticker!r}"
    )
