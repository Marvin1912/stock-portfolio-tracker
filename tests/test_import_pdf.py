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


def _stub_pdf_db(
    *,
    known_tickers: dict[str, int],
    existing_uuids: set[str] | None = None,
) -> AsyncMock:
    """Mock DB for ImportService that:
    - Returns a Stock for ``known_tickers`` (and None otherwise),
    - Returns a hit on ``existing_uuids`` to simulate dedup,
    - No-ops everything else, including the recompute path.
    """
    existing_uuids = existing_uuids or set()
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.delete = AsyncMock()

    async def _execute(stmt):  # type: ignore[no-untyped-def]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        result = MagicMock()

        if "external_uuid" in compiled:
            seen = next((u for u in existing_uuids if u in compiled), None)
            result.scalar_one_or_none.return_value = 1 if seen else None
            return result

        if "stock.ticker" in compiled or "stock\".\"ticker" in compiled:
            ticker = next((t for t in known_tickers if f"'{t}'" in compiled), None)
            if ticker:
                stock = MagicMock()
                stock.id = known_tickers[ticker]
                stock.ticker = ticker
                stock.currency = "EUR"
                result.scalar_one_or_none.return_value = stock
            else:
                result.scalar_one_or_none.return_value = None
            return result

        # recompute_holdings' aggregate / holdings queries: return empty.
        result.all.return_value = []
        result.scalars.return_value.all.return_value = []
        result.scalar_one_or_none.return_value = None
        return result

    db.execute = AsyncMock(side_effect=_execute)
    return db


@pytest.mark.asyncio
async def test_import_from_holdings_inserts_transaction() -> None:
    from app.services.import_service import ImportService

    db = _stub_pdf_db(known_tickers={"AAPL": 1})

    service = ImportService()
    with patch(
        "app.services.import_service.ensure_prices_cached",
        new=AsyncMock(return_value=[]),
    ):
        result = await service.import_from_holdings(
            [("AAPL", Decimal("5"))], db, source_file="report.pdf"
        )

    assert result == [("AAPL", Decimal("5"))]
    added = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "Transaction"
    ]
    assert len(added) == 1
    tx = added[0]
    assert tx.type == "BUY"
    assert tx.source == "PDF"
    assert tx.external_uuid == "pdf:report.pdf:0"
    assert tx.shares == Decimal("5")


@pytest.mark.asyncio
async def test_import_from_holdings_is_idempotent_on_reimport() -> None:
    """Re-running the same PDF skips the insert (uuid already exists)."""
    from app.services.import_service import ImportService

    db = _stub_pdf_db(
        known_tickers={"MSFT": 2},
        existing_uuids={"pdf:report.pdf:0"},
    )

    service = ImportService()
    with patch(
        "app.services.import_service.ensure_prices_cached",
        new=AsyncMock(return_value=[]),
    ):
        result = await service.import_from_holdings(
            [("MSFT", Decimal("3"))], db, source_file="report.pdf"
        )

    assert result == [("MSFT", Decimal("3"))]
    added_tx = [
        call.args[0]
        for call in db.add.call_args_list
        if call.args[0].__class__.__name__ == "Transaction"
    ]
    assert added_tx == []  # nothing inserted on second run


@pytest.mark.asyncio
async def test_import_from_holdings_caches_prices_for_imported_tickers() -> None:
    """A freshly imported ticker triggers a price-cache warmup so it shows a
    value immediately instead of waiting for the daily scheduler."""
    from app.services.import_service import ImportService

    db = _stub_pdf_db(known_tickers={"AAPL": 1})

    service = ImportService()
    with patch(
        "app.services.import_service.ensure_prices_cached",
        new=AsyncMock(return_value=["AAPL"]),
    ) as mock_ensure:
        await service.import_from_holdings(
            [("AAPL", Decimal("5"))], db, source_file="report.pdf"
        )

    mock_ensure.assert_awaited_once()
    tickers = mock_ensure.call_args.args[0]
    assert set(tickers) == {"AAPL"}


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

    mock_inner.assert_called_once_with(pairs, db, source_file="fake.pdf")
    assert result == pairs
