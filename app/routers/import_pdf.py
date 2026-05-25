"""PDF import UI: upload, preview, and confirm."""

from __future__ import annotations

import datetime
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.transaction import TX_TYPE_BUY
from app.services.comdirect_parser import ComdirectParser, ParsedTrade
from app.services.generic_parser import GenericTableParser
from app.services.import_service import ImportService
from app.services.openfigi_lookup import resolve_isin, resolve_wkn

router = APIRouter(prefix="/import", tags=["import"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)
_parser = GenericTableParser()
_comdirect = ComdirectParser()
_service = ImportService()


def _decimal_or_zero(raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal("0")


def _render(request: Request, name: str, context: dict) -> HTMLResponse:  # type: ignore[type-arg]
    context["request"] = request
    return templates.TemplateResponse(request=request, name=name, context=context)


# ---------------------------------------------------------------------------
# Upload form
# ---------------------------------------------------------------------------


@router.get("/pdf", response_class=HTMLResponse)
async def import_pdf_page(request: Request) -> HTMLResponse:
    """Render the PDF upload form."""
    return _render(request, "import_pdf.html", {"step": "upload"})


# ---------------------------------------------------------------------------
# Parse & preview
# ---------------------------------------------------------------------------


@router.post("/pdf", response_class=HTMLResponse)
async def import_pdf_preview(
    request: Request,
    file: UploadFile,
) -> HTMLResponse:
    """Accept a broker PDF, extract holdings, and show a preview."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return _render(
            request,
            "import_pdf.html",
            {"step": "upload", "error": "Please upload a valid PDF file."},
        )

    contents = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        trade = _comdirect.extract_trade(tmp_path)
        if trade is not None:
            return await _preview_comdirect(request, trade)
        pairs = _parser.extract(tmp_path)
    except Exception:
        return _render(
            request,
            "import_pdf.html",
            {"step": "upload", "error": "Unrecognized PDF format or could not parse the file."},
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    if not pairs:
        return _render(
            request,
            "import_pdf.html",
            {"step": "upload", "error": "No holdings found in the uploaded PDF."},
        )

    return _render(
        request,
        "import_pdf.html",
        {"step": "preview", "pairs": pairs},
    )


async def _preview_comdirect(request: Request, trade: ParsedTrade) -> HTMLResponse:
    """Resolve the WKN/ISIN to a ticker and render the rich trade preview."""
    key = getattr(getattr(request.app.state, "settings", None), "openfigi_api_key", "")
    ticker: str | None = None
    if trade.wkn:
        ticker = await resolve_wkn(trade.wkn, key)
    if ticker is None and trade.isin:
        ticker = await resolve_isin(trade.isin, key)

    return _render(
        request,
        "import_pdf.html",
        {"step": "preview_trade", "trade": trade, "ticker": ticker},
    )


# ---------------------------------------------------------------------------
# Confirm & commit
# ---------------------------------------------------------------------------


@router.post("/pdf/confirm", response_class=HTMLResponse)
async def import_pdf_confirm(
    request: Request,
    tickers: Annotated[list[str], Form()],
    quantities: Annotated[list[str], Form()],
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Commit the previewed holdings into the database."""
    pairs: list[tuple[str, Decimal]] = []
    for ticker, qty_str in zip(tickers, quantities, strict=False):
        try:
            pairs.append((ticker.strip().upper(), Decimal(qty_str)))
        except InvalidOperation:
            continue

    if not pairs:
        return _render(
            request,
            "import_pdf.html",
            {"step": "upload", "error": "No valid holdings to import."},
        )

    processed = await _service.import_from_holdings(pairs, db)

    return _render(
        request,
        "import_pdf.html",
        {"step": "done", "processed": processed},
    )


@router.post("/pdf/confirm-trade", response_class=HTMLResponse)
async def import_pdf_confirm_trade(
    request: Request,
    ticker: Annotated[str, Form()],
    trade_type: Annotated[str, Form()],
    shares: Annotated[str, Form()],
    amount: Annotated[str, Form()],
    fee: Annotated[str, Form()],
    tax: Annotated[str, Form()],
    currency: Annotated[str, Form()],
    date: Annotated[str, Form()],
    name: Annotated[str, Form()] = "",
    wkn: Annotated[str, Form()] = "",
    isin: Annotated[str, Form()] = "",
    order_ref: Annotated[str, Form()] = "",
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Commit a previewed comdirect trade as a full transaction."""
    ticker = ticker.strip().upper()
    if not ticker:
        return _render(
            request,
            "import_pdf.html",
            {"step": "done", "processed": []},
        )

    try:
        trade_date = datetime.datetime.fromisoformat(date)
    except ValueError:
        trade_date = datetime.datetime.now(datetime.UTC)

    shares_dec = _decimal_or_zero(shares)
    trade = ParsedTrade(
        trade_type=trade_type.strip().upper() or TX_TYPE_BUY,
        name=name or None,
        wkn=wkn or None,
        isin=isin or None,
        shares=shares_dec,
        price=None,
        amount=_decimal_or_zero(amount),
        fee=_decimal_or_zero(fee),
        tax=_decimal_or_zero(tax),
        currency=currency.strip().upper() or "EUR",
        date=trade_date,
        order_ref=order_ref or None,
    )

    status = await _service.import_trade(trade, ticker, db)

    messages = {
        "duplicate": (
            f"This {trade.trade_type} of {shares_dec} {ticker} on "
            f"{trade.date.date()} is already in your portfolio "
            "(matched an existing transaction) — skipped to avoid a duplicate."
        ),
        "unknown_ticker": (
            f"{ticker} is not tracked in your portfolio yet, so the trade was "
            "not imported."
        ),
    }
    processed = [(ticker, shares_dec)] if status == "created" else []
    return _render(
        request,
        "import_pdf.html",
        {"step": "done", "processed": processed, "message": messages.get(status)},
    )
