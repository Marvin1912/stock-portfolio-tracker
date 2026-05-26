"""Tests for the /api/v1/holdings CRUD endpoints."""

from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient

from app.services.portfolio_service import PortfolioService


def _vline_shapes(fig: dict) -> list[dict]:
    """Vertical-line shapes (x0 == x1) from a Plotly figure dict."""
    return [s for s in fig.get("layout", {}).get("shapes", []) if s.get("x0") == s.get("x1")]


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


# ---------------------------------------------------------------------------
# Year-boundary separators on the time-series charts
# ---------------------------------------------------------------------------


def _series(*dates: datetime.date) -> list[tuple[datetime.date, Decimal]]:
    return [(d, Decimal("100")) for d in dates]


async def test_performance_chart_draws_year_boundary(client: AsyncClient) -> None:
    """A series spanning into a new year gets a Jan-1 separator + year label."""
    history = _series(
        datetime.date(2025, 6, 1),
        datetime.date(2026, 1, 1),
        datetime.date(2026, 5, 1),
    )
    with patch.object(
        PortfolioService,
        "earliest_transaction_date",
        new=AsyncMock(return_value=datetime.date(2025, 6, 1)),
    ), patch.object(
        PortfolioService, "get_performance_history", new=AsyncMock(return_value=history)
    ):
        response = await client.get("/api/v1/holdings/chart/performance")

    fig = response.json()
    vlines = _vline_shapes(fig)
    assert [s["x0"] for s in vlines] == ["2026-01-01"]
    assert {a["text"] for a in fig["layout"].get("annotations", [])} == {"2026"}


async def test_performance_chart_no_boundary_within_single_year(
    client: AsyncClient,
) -> None:
    """A series wholly inside one calendar year has no separators."""
    history = _series(datetime.date(2025, 2, 1), datetime.date(2025, 11, 1))
    with patch.object(
        PortfolioService,
        "earliest_transaction_date",
        new=AsyncMock(return_value=datetime.date(2025, 2, 1)),
    ), patch.object(
        PortfolioService, "get_performance_history", new=AsyncMock(return_value=history)
    ):
        response = await client.get("/api/v1/holdings/chart/performance")

    assert _vline_shapes(response.json()) == []


async def test_gain_loss_chart_draws_boundaries_per_year(client: AsyncClient) -> None:
    """The P/L chart marks each Jan-1 in the span and keeps its y=0 baseline."""
    history = _series(
        datetime.date(2024, 3, 1),
        datetime.date(2025, 1, 1),
        datetime.date(2026, 1, 1),
        datetime.date(2026, 4, 1),
    )
    with patch.object(
        PortfolioService, "get_gain_loss_history", new=AsyncMock(return_value=history)
    ):
        response = await client.get("/api/v1/holdings/chart/gain-loss")

    fig = response.json()
    assert [s["x0"] for s in _vline_shapes(fig)] == ["2025-01-01", "2026-01-01"]
    # The break-even baseline is a horizontal shape (y0 == y1) and must survive.
    shapes = fig["layout"]["shapes"]
    assert any(s.get("y0") == s.get("y1") == 0 for s in shapes)
