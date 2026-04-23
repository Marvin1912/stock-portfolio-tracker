"""Tests for the GET /api/v1/holdings/summary endpoint."""

from __future__ import annotations

from unittest.mock import MagicMock

from httpx import AsyncClient


def _empty_holdings_result() -> MagicMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    return result


async def test_summary_empty(client: AsyncClient, mock_session: MagicMock) -> None:
    mock_session.execute.return_value = _empty_holdings_result()

    response = await client.get("/api/v1/holdings/summary")
    assert response.status_code == 200
    data = response.json()
    assert data["holdings"] == []
    assert data["total_value"] is None


async def test_summary_response_shape(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    """Summary endpoint returns expected top-level keys."""
    mock_session.execute.return_value = _empty_holdings_result()

    response = await client.get("/api/v1/holdings/summary")
    assert response.status_code == 200
    data = response.json()
    assert "holdings" in data
    assert "total_value" in data
