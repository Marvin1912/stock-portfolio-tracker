"""Tests for the portfolio overview HTML page."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_portfolio_page_returns_html(require_db: None, client: AsyncClient) -> None:
    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_portfolio_page_contains_expected_elements(
    require_db: None, client: AsyncClient
) -> None:
    response = await client.get("/")
    html = response.text
    assert "Portfolio Overview" in html
    assert "Stock" in html
    assert "Quantity" in html
    assert "Current Value" in html
    assert "Total Portfolio Value" in html
