"""Tests for the stock detail HTML page, including the transactions list."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import MagicMock

from httpx import AsyncClient

from app.models.stock import Stock
from app.models.transaction import Transaction


def _stock() -> Stock:
    stock = Stock(id=1, ticker="AAPL", name="Apple Inc.", currency="USD")
    return stock


def _result(scalar_one: object = None, scalars_all: list | None = None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_one
    result.scalars.return_value.all.return_value = scalars_all or []
    return result


def _configure(mock_session: MagicMock, transactions: list[Transaction]) -> None:
    """Wire the four db.execute() calls the stock_detail route makes in order.

    1. Stock lookup, 2. Holding lookup, 3. latest close price, 4. transactions.
    """
    mock_session.execute.side_effect = [
        _result(scalar_one=_stock()),  # stock
        _result(scalar_one=None),  # holding
        _result(scalar_one=None),  # latest close
        _result(scalars_all=transactions),  # transactions
    ]


async def test_stock_detail_lists_transactions(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    tx = Transaction(
        stock_id=1,
        date=datetime.datetime(2026, 1, 15, tzinfo=datetime.UTC),
        type="BUY",
        shares=Decimal("10"),
        amount=Decimal("1500.00"),
        currency="USD",
        fee=Decimal("1.50"),
        tax=Decimal("0"),
        note="initial position",
        source="MANUAL",
    )
    _configure(mock_session, [tx])

    response = await client.get("/stocks/AAPL")
    html = response.text

    assert response.status_code == 200
    assert "Transactions" in html
    assert "2026-01-15" in html
    assert "BUY" in html
    assert "1500.00" in html
    assert "initial position" in html


async def test_stock_detail_empty_transactions(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    _configure(mock_session, [])

    response = await client.get("/stocks/AAPL")
    html = response.text

    assert response.status_code == 200
    assert "no transactions recorded" in html
