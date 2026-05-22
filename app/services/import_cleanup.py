"""Delete every transaction (and orphan stock) that came from an XML import.

Intended for the "Clear XML-imported data" admin button on the import page —
lets the user wipe a botched import without touching manual or PDF-sourced
rows.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock
from app.models.transaction import TX_SOURCE_XML, Transaction
from app.services.holdings_service import recompute_holdings


@dataclass(slots=True)
class CleanupSummary:
    deleted_transactions: int
    deleted_stocks: int


async def clear_xml_imports(db: AsyncSession) -> CleanupSummary:
    """Delete XML transactions, recompute affected holdings, drop orphan stocks."""
    affected_ids_result = await db.execute(
        select(Transaction.stock_id)
        .where(Transaction.source == TX_SOURCE_XML)
        .where(Transaction.stock_id.is_not(None))
        .distinct()
    )
    affected_stock_ids: set[int] = {row[0] for row in affected_ids_result.all()}

    deleted_tx_result = await db.execute(
        delete(Transaction).where(Transaction.source == TX_SOURCE_XML)
    )
    deleted_transactions = deleted_tx_result.rowcount or 0

    deleted_stocks = 0
    if affected_stock_ids:
        await recompute_holdings(db, affected_stock_ids)

        orphan_filter = ~exists().where(Transaction.stock_id == Stock.id)
        deleted_stock_result = await db.execute(
            delete(Stock)
            .where(Stock.id.in_(affected_stock_ids))
            .where(orphan_filter)
        )
        deleted_stocks = deleted_stock_result.rowcount or 0

    await db.flush()
    return CleanupSummary(
        deleted_transactions=deleted_transactions,
        deleted_stocks=deleted_stocks,
    )
