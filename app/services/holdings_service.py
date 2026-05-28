"""Materialise the ``holding`` table from the ``transaction`` history.

After issue #96 lands, the transaction table is the source of truth.
``Holding`` becomes a derived snapshot of net positions per stock that we
recompute whenever transactions are added or changed.
"""

from __future__ import annotations

import datetime
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.holding import Holding
from app.models.transaction import Transaction

# Position-affecting transaction types.  Dividends, fees, and taxes do not
# change the share count.
_POSITIVE = ("BUY", "TRANSFER_IN")
_NEGATIVE = ("SELL", "TRANSFER_OUT")


async def _net_positions(
    db: AsyncSession,
    stock_ids: Iterable[int] | None,
) -> dict[int, Decimal]:
    """Return ``{stock_id: net_shares}`` aggregated from the transaction table.

    When ``stock_ids`` is None, every stock with a position-affecting
    transaction is included.
    """
    signed = case(
        (Transaction.type.in_(_POSITIVE), Transaction.shares),
        (Transaction.type.in_(_NEGATIVE), -Transaction.shares),
        else_=Decimal("0"),
    )
    stmt = (
        select(Transaction.stock_id, func.coalesce(func.sum(signed), 0))
        .where(Transaction.stock_id.is_not(None))
        .group_by(Transaction.stock_id)
    )
    id_list = list(stock_ids) if stock_ids is not None else None
    if id_list is not None:
        if not id_list:
            return {}
        stmt = stmt.where(Transaction.stock_id.in_(id_list))

    result = await db.execute(stmt)
    out: dict[int, Decimal] = {}
    for stock_id, net in result.all():
        if stock_id is None:
            continue
        out[stock_id] = Decimal(net or 0)
    return out


async def recompute_holdings(
    db: AsyncSession,
    stock_ids: Iterable[int] | None = None,
) -> None:
    """Recompute the ``holding`` snapshot from transactions.

    For each affected stock_id, the holding's ``quantity`` is set to the
    net share total.  Holdings that net to zero are deleted.  When
    ``stock_ids`` is None, the entire holding table is rebuilt from
    scratch.
    """
    id_list = list(stock_ids) if stock_ids is not None else None
    net_by_stock = await _net_positions(db, id_list)

    holding_stmt = select(Holding)
    if id_list is not None:
        if not id_list and not net_by_stock:
            return
        if id_list:
            holding_stmt = holding_stmt.where(Holding.stock_id.in_(id_list))
    existing_rows = await db.execute(holding_stmt)
    existing: dict[int, Holding] = {
        h.stock_id: h for h in existing_rows.scalars().all()
    }

    target_ids: set[int] = set(net_by_stock.keys()) | set(existing.keys())

    for sid in target_ids:
        net = net_by_stock.get(sid, Decimal("0"))
        holding = existing.get(sid)
        if net == Decimal("0"):
            if holding is not None:
                await db.delete(holding)
            continue
        if holding is None:
            db.add(Holding(stock_id=sid, quantity=net))
        else:
            holding.quantity = net

    await db.flush()


async def net_shares_by_stock(
    db: AsyncSession,
    stock_ids: Iterable[int] | None = None,
) -> dict[int, Decimal]:
    """Public read-only helper exposing the aggregated net positions.

    Used by the performance-history walk in issue #99.
    """
    return await _net_positions(db, stock_ids)


async def net_shares_as_of_date(
    db: AsyncSession,
    as_of: datetime.date,
    stock_ids: Iterable[int] | None = None,
) -> dict[int, Decimal]:
    """Return ``{stock_id: net_shares}`` considering only transactions with date <= as_of."""
    signed = case(
        (Transaction.type.in_(_POSITIVE), Transaction.shares),
        (Transaction.type.in_(_NEGATIVE), -Transaction.shares),
        else_=Decimal("0"),
    )
    stmt = (
        select(Transaction.stock_id, func.coalesce(func.sum(signed), 0))
        .where(
            Transaction.stock_id.is_not(None),
            cast(Transaction.date, Date) <= as_of,
        )
        .group_by(Transaction.stock_id)
    )
    id_list = list(stock_ids) if stock_ids is not None else None
    if id_list is not None:
        if not id_list:
            return {}
        stmt = stmt.where(Transaction.stock_id.in_(id_list))

    result = await db.execute(stmt)
    return {sid: Decimal(net or 0) for sid, net in result.all() if sid is not None}


__all__ = ["recompute_holdings", "net_shares_by_stock", "net_shares_as_of_date"]
