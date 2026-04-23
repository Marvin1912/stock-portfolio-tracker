"""Tests for the portfolio overview HTML page."""

from __future__ import annotations

from unittest.mock import MagicMock

from httpx import AsyncClient


def _configure_empty_portfolio(mock_session: MagicMock) -> None:
    """Wire the session so get_summary returns no holdings and last_refresh is None.

    The route issues two db.execute() calls: one for the holdings select and
    one for the max(PriceCache.date) scalar.
    """
    holdings_result = MagicMock()
    holdings_result.scalars.return_value.all.return_value = []

    last_refresh_result = MagicMock()
    last_refresh_result.scalar.return_value = None

    mock_session.execute.side_effect = [holdings_result, last_refresh_result]


async def test_portfolio_page_returns_html(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    _configure_empty_portfolio(mock_session)

    response = await client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


async def test_portfolio_page_contains_expected_elements(
    client: AsyncClient, mock_session: MagicMock
) -> None:
    _configure_empty_portfolio(mock_session)

    response = await client.get("/")
    html = response.text
    assert "Portfolio Overview" in html
    assert "Stock" in html
    assert "Quantity" in html
    assert "Current Value" in html
    assert "Total Portfolio Value" in html
