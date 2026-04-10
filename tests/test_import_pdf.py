"""Tests for the PDF import UI endpoints."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

FIXTURE_PDF = Path(__file__).parent / "fixtures" / "sample_holdings.pdf"


# ---------------------------------------------------------------------------
# GET /import/pdf  — upload form
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_pdf_get_renders_upload_form(client: AsyncClient) -> None:
    response = await client.get("/import/pdf")
    assert response.status_code == 200
    assert "Upload broker statement" in response.text
    assert 'enctype="multipart/form-data"' in response.text


# ---------------------------------------------------------------------------
# POST /import/pdf  — preview step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_pdf_post_non_pdf_returns_error(client: AsyncClient) -> None:
    response = await client.post(
        "/import/pdf",
        files={"file": ("report.txt", b"not a pdf", "text/plain")},
    )
    assert response.status_code == 200
    assert "valid PDF" in response.text


@pytest.mark.asyncio
async def test_import_pdf_post_unreadable_pdf_returns_error(client: AsyncClient) -> None:
    response = await client.post(
        "/import/pdf",
        files={"file": ("bad.pdf", b"garbage data", "application/pdf")},
    )
    assert response.status_code == 200
    assert "Unrecognized PDF" in response.text or "No holdings" in response.text


@pytest.mark.asyncio
async def test_import_pdf_post_valid_pdf_shows_preview(client: AsyncClient) -> None:
    pdf_bytes = FIXTURE_PDF.read_bytes()
    response = await client.post(
        "/import/pdf",
        files={"file": ("holdings.pdf", pdf_bytes, "application/pdf")},
    )
    assert response.status_code == 200
    assert "Preview extracted holdings" in response.text
    assert "AAPL" in response.text
    assert "Confirm Import" in response.text


@pytest.mark.asyncio
async def test_import_pdf_post_valid_pdf_shows_all_tickers(client: AsyncClient) -> None:
    pdf_bytes = FIXTURE_PDF.read_bytes()
    response = await client.post(
        "/import/pdf",
        files={"file": ("holdings.pdf", pdf_bytes, "application/pdf")},
    )
    assert "AAPL" in response.text
    assert "MSFT" in response.text
    assert "GOOGL" in response.text


# ---------------------------------------------------------------------------
# POST /import/pdf/confirm  — commit step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_pdf_confirm_calls_import_service(client: AsyncClient) -> None:
    processed = [("AAPL", Decimal("10")), ("MSFT", Decimal("5.5"))]

    with patch(
        "app.routers.import_pdf._service.import_from_holdings",
        new=AsyncMock(return_value=processed),
    ):
        response = await client.post(
            "/import/pdf/confirm",
            data={
                "tickers": ["AAPL", "MSFT"],
                "quantities": ["10", "5.5"],
            },
        )

    assert response.status_code == 200
    assert "Import complete" in response.text
    assert "AAPL" in response.text
    assert "MSFT" in response.text
    assert "2 holding" in response.text


@pytest.mark.asyncio
async def test_import_pdf_confirm_no_processed_shows_warning(client: AsyncClient) -> None:
    with patch(
        "app.routers.import_pdf._service.import_from_holdings",
        new=AsyncMock(return_value=[]),
    ):
        response = await client.post(
            "/import/pdf/confirm",
            data={
                "tickers": ["UNKNOWN"],
                "quantities": ["1"],
            },
        )

    assert response.status_code == 200
    assert "No holdings were imported" in response.text


@pytest.mark.asyncio
async def test_import_pdf_confirm_invalid_quantity_skipped(client: AsyncClient) -> None:
    """Invalid quantity entries are skipped; valid ones are passed through."""
    processed = [("AAPL", Decimal("5"))]

    with patch(
        "app.routers.import_pdf._service.import_from_holdings",
        new=AsyncMock(return_value=processed),
    ) as mock_import:
        response = await client.post(
            "/import/pdf/confirm",
            data={
                "tickers": ["AAPL", "MSFT"],
                "quantities": ["5", "not-a-number"],
            },
        )

    assert response.status_code == 200
    # Only AAPL with valid quantity should have been passed
    call_pairs = mock_import.call_args[0][0]
    assert len(call_pairs) == 1
    assert call_pairs[0] == ("AAPL", Decimal("5"))


@pytest.mark.asyncio
async def test_import_pdf_confirm_all_invalid_returns_error(client: AsyncClient) -> None:
    response = await client.post(
        "/import/pdf/confirm",
        data={
            "tickers": ["AAPL"],
            "quantities": ["bad"],
        },
    )
    assert response.status_code == 200
    assert "No valid holdings" in response.text


# ---------------------------------------------------------------------------
# ImportService.import_from_holdings — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_from_holdings_creates_new_holding() -> None:
    from app.services.import_service import ImportService

    stock = MagicMock()
    stock.id = 1

    stock_result = MagicMock()
    stock_result.scalar_one_or_none.return_value = stock

    holding_result = MagicMock()
    holding_result.scalar_one_or_none.return_value = None

    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(side_effect=[stock_result, holding_result])

    service = ImportService()
    result = await service.import_from_holdings([("AAPL", Decimal("5"))], db)

    assert result == [("AAPL", Decimal("5"))]
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_import_from_holdings_increases_existing_holding() -> None:
    from app.services.import_service import ImportService

    stock = MagicMock()
    stock.id = 2

    holding = MagicMock()
    holding.quantity = Decimal("10")

    stock_result = MagicMock()
    stock_result.scalar_one_or_none.return_value = stock

    holding_result = MagicMock()
    holding_result.scalar_one_or_none.return_value = holding

    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(side_effect=[stock_result, holding_result])

    service = ImportService()
    result = await service.import_from_holdings([("MSFT", Decimal("3"))], db)

    assert result == [("MSFT", Decimal("3"))]
    assert holding.quantity == Decimal("13")
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_import_from_pdf_delegates_to_import_from_holdings() -> None:
    """import_from_pdf should extract pairs and call import_from_holdings."""
    from app.services.import_service import ImportService

    service = ImportService()
    pairs = [("AAPL", Decimal("10"))]

    parser = MagicMock()
    parser.extract.return_value = pairs

    db = AsyncMock()

    with patch.object(
        service, "import_from_holdings", new=AsyncMock(return_value=pairs)
    ) as mock_inner:
        result = await service.import_from_pdf(Path("/fake.pdf"), parser, db)

    mock_inner.assert_called_once_with(pairs, db)
    assert result == pairs
