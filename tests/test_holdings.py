"""Tests for the /api/v1/holdings CRUD endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_list_holdings_empty(client: AsyncClient) -> None:
    response = await client.get("/api/v1/holdings")
    assert response.status_code == 200
    assert response.json() == []


async def test_create_holding_unknown_ticker(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/holdings", json={"ticker": "UNKNOWN", "quantity": "10.0"}
    )
    assert response.status_code == 404


async def test_delete_holding_not_found(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/holdings/99999")
    assert response.status_code == 404


async def test_update_holding_not_found(client: AsyncClient) -> None:
    response = await client.put(
        "/api/v1/holdings/99999", json={"quantity": "5.0"}
    )
    assert response.status_code == 404
