"""Tests for the /health endpoint."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_health_returns_200(client: AsyncClient) -> None:
    """The /health endpoint must respond with HTTP 200."""
    response = await client.get("/health")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_health_response_schema(client: AsyncClient) -> None:
    """The /health response body must contain status, version, and environment."""
    response = await client.get("/health")
    body = response.json()

    assert body["status"] == "ok"
    assert "version" in body
    assert body["environment"] == "development"
