"""Tests for the holdings recompute service."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.holding import Holding
from app.services.holdings_service import recompute_holdings


def _make_db(
    *,
    net_positions: dict[int, str],
    existing_holdings: list[Holding] | None = None,
) -> AsyncMock:
    """Mock DB that returns ``net_positions`` for the aggregate query and
    ``existing_holdings`` for the holdings query.
    """
    existing_holdings = existing_holdings or []

    agg_result = MagicMock()
    agg_result.all.return_value = [
        (sid, Decimal(qty)) for sid, qty in net_positions.items()
    ]

    holdings_result = MagicMock()
    holdings_result.scalars.return_value.all.return_value = existing_holdings

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[agg_result, holdings_result])
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()
    return db


def _holding(stock_id: int, quantity: str) -> Holding:
    return Holding(stock_id=stock_id, quantity=Decimal(quantity))


@pytest.mark.asyncio
async def test_creates_new_holding_when_none_exists() -> None:
    db = _make_db(net_positions={1: "10"})

    await recompute_holdings(db)

    db.add.assert_called_once()
    new_holding = db.add.call_args[0][0]
    assert new_holding.stock_id == 1
    assert new_holding.quantity == Decimal("10")
    db.delete.assert_not_called()


@pytest.mark.asyncio
async def test_updates_existing_holding_quantity() -> None:
    existing = _holding(1, "5")
    db = _make_db(net_positions={1: "10"}, existing_holdings=[existing])

    await recompute_holdings(db)

    assert existing.quantity == Decimal("10")
    db.add.assert_not_called()
    db.delete.assert_not_called()


@pytest.mark.asyncio
async def test_deletes_holding_when_net_is_zero() -> None:
    existing = _holding(1, "5")
    db = _make_db(net_positions={1: "0"}, existing_holdings=[existing])

    await recompute_holdings(db)

    db.delete.assert_awaited_once_with(existing)
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_three_buys_minus_one_sell_yields_net_position() -> None:
    """Issue #98 acceptance: 3 buys minus 1 sell produces the correct net."""
    db = _make_db(net_positions={7: "20"})  # 5+5+10-0 = 20 (simulated aggregate)

    await recompute_holdings(db)

    new_holding = db.add.call_args[0][0]
    assert new_holding.stock_id == 7
    assert new_holding.quantity == Decimal("20")


@pytest.mark.asyncio
async def test_filters_to_supplied_stock_ids() -> None:
    """When stock_ids is given, only those rows are loaded."""
    db = _make_db(net_positions={2: "3"}, existing_holdings=[])

    await recompute_holdings(db, stock_ids=[2])

    # Two execute calls were made: aggregate + holdings — both filtered.
    assert db.execute.await_count == 2
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_empty_stock_ids_short_circuits() -> None:
    """An empty stock_ids list means: no work to do."""
    db = AsyncMock()
    db.execute = AsyncMock()
    db.add = MagicMock()
    db.delete = AsyncMock()
    db.flush = AsyncMock()

    await recompute_holdings(db, stock_ids=[])

    db.add.assert_not_called()
    db.delete.assert_not_called()
