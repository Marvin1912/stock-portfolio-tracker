"""Tests for the /api/v1/holdings CRUD endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

from httpx import AsyncClient


async def test_list_holdings_empty(client: AsyncClient, mock_session: MagicMock) -> None:
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    mock_session.execute.return_value = result

    response = await client.get("/api/v1/holdings")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_holding_unknown_ticker(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    mock_session.execute.return_value = result

    response = await client.post(
        "/api/v1/holdings", json={"ticker": "UNKNOWN", "quantity": "10.0"}
    )
    assert response.status_code == 404


async def test_delete_holding_not_found(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    mock_session.get.return_value = None

    response = await client.delete("/api/v1/holdings/99999")
    assert response.status_code == 404


async def test_update_holding_not_found(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    mock_session.get.return_value = None

    response = await client.put(
        "/api/v1/holdings/99999", json={"quantity": "5.0"}
    )
    assert response.status_code == 404
