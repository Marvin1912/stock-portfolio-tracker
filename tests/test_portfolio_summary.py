"""Tests for the GET /api/v1/holdings/summary endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_summary_empty(require_db: None, client: AsyncClient) -> None:
    response = await client.get("/api/v1/holdings/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["holdings"] == []
    assert data["total_value"] is None


async def test_summary_response_shape(require_db: None, client: AsyncClient) -> None:
    """Summary endpoint returns expected top-level keys."""
    response = await client.get("/api/v1/holdings/summary")
    assert response.status_code == 200
    data = response.json()
    assert "holdings" in data
    assert "total_value" in data
