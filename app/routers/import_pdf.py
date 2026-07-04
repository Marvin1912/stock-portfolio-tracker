"""PDF import UI: upload, preview, and confirm."""

from __future__ import annotations

import datetime
import logging
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.models.transaction import TX_TYPE_BUY
from app.services import batch_pdf_cache
from app.services.batch_pdf_cache import BatchPdfItem
from app.services.comdirect_dividend_parser import ComdirectDividendParser
from app.services.comdirect_parser import ComdirectParser, ParsedTrade
from app.services.generic_parser import GenericTableParser
from app.services.import_service import ImportService
from app.services.ing_parser import IngParser
from app.services.openfigi_lookup import resolve_isin, resolve_wkn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/import", tags=["import"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)
_parser = GenericTableParser()
_comdirect = ComdirectParser()
_ing = IngParser()
_comdirect_dividend = ComdirectDividendParser()
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
    files: list[UploadFile],
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Accept one or more broker PDFs, extract trades/holdings, and show a preview."""
    if not files or all(not f.filename for f in files):
        return _render(
            request,
            "import_pdf.html",
            {"step": "upload", "error": "Please upload at least one PDF file."},
        )

    invalid = [f.filename for f in files if f.filename and not f.filename.lower().endswith(".pdf")]
    if invalid:
        return _render(
            request,
            "import_pdf.html",
            {"step": "upload", "error": f"Please upload valid PDF files only. Not accepted: {', '.join(invalid)}"},
        )

    # Single-file path — existing flow, untouched.
    if len(files) == 1:
        return await _handle_single_file(request, files[0], db)

    # Multi-file batch path.
    return await _handle_batch(request, files, db)


async def _handle_single_file(
    request: Request, file: UploadFile, db: AsyncSession
) -> HTMLResponse:
    """Original single-file flow: comdirect trade or generic holdings preview."""
    contents = await file.read()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(contents)
        tmp_path = Path(tmp.name)

    try:
        t0 = time.perf_counter()
        trade = (
            _comdirect.extract_trade(tmp_path)
            or _ing.extract_trade(tmp_path)
            or _comdirect_dividend.extract_trade(tmp_path)
        )
        logger.info(
            "PDF import: broker trade parse took %.2fs (%d bytes, matched=%s)",
            time.perf_counter() - t0,
            len(contents),
            trade is not None,
        )
        if trade is not None:
            return await _preview_comdirect(request, trade, db)
        t0 = time.perf_counter()
        pairs = _parser.extract(tmp_path)
        logger.info(
            "PDF import: generic parse took %.2fs (%d pairs)",
            time.perf_counter() - t0,
            len(pairs),
        )
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


async def _handle_batch(
    request: Request,
    files: list[UploadFile],
    db: AsyncSession,
) -> HTMLResponse:
    """Parse multiple PDFs, check duplicates, cache results, and show batch overview."""
    key = getattr(getattr(request.app.state, "settings", None), "openfigi_api_key", "")
    items: list[BatchPdfItem] = []

    for file in files:
        filename = file.filename or "unknown.pdf"
        contents = await file.read()

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(contents)
            tmp_path = Path(tmp.name)

        trade: ParsedTrade | None = None
        pairs: list[tuple[str, Decimal]] | None = None
        parse_error: str | None = None

        try:
            trade = (
                _comdirect.extract_trade(tmp_path)
                or _ing.extract_trade(tmp_path)
                or _comdirect_dividend.extract_trade(tmp_path)
            )
            if trade is None:
                pairs = _parser.extract(tmp_path) or None
                if pairs is None:
                    parse_error = "No holdings found in this PDF."
        except Exception as exc:
            parse_error = f"Could not parse: {exc}"
        finally:
            tmp_path.unlink(missing_ok=True)

        ticker: str | None = None
        is_duplicate: bool | None = None

        if trade is not None:
            t0 = time.perf_counter()
            if trade.wkn:
                ticker = await resolve_wkn(trade.wkn, key)
            if ticker is None and trade.isin:
                ticker = await resolve_isin(trade.isin, key)
            logger.info(
                "PDF batch import: ticker resolution for %s took %.2fs -> %s",
                filename,
                time.perf_counter() - t0,
                ticker,
            )
            if ticker is not None:
                is_duplicate = await _service.check_is_duplicate(trade, ticker, db)

        items.append(
            BatchPdfItem(
                filename=filename,
                trade=trade,
                ticker=ticker,
                is_duplicate=is_duplicate,
                pairs=pairs,
                parse_error=parse_error,
            )
        )

    token = batch_pdf_cache.store(items)
    return _render(
        request,
        "import_pdf.html",
        {"step": "preview_batch", "items": items, "token": token, "enumerate": enumerate},
    )


async def _preview_comdirect(
    request: Request, trade: ParsedTrade, db: AsyncSession
) -> HTMLResponse:
    """Resolve the WKN/ISIN to a ticker and render the rich trade preview."""
    key = getattr(getattr(request.app.state, "settings", None), "openfigi_api_key", "")
    ticker: str | None = None
    t0 = time.perf_counter()
    if trade.wkn:
        ticker = await resolve_wkn(trade.wkn, key)
        logger.info(
            "PDF import: OpenFIGI resolve_wkn(%s) took %.2fs -> %s",
            trade.wkn,
            time.perf_counter() - t0,
            ticker,
        )
    if ticker is None and trade.isin:
        t1 = time.perf_counter()
        ticker = await resolve_isin(trade.isin, key)
        logger.info(
            "PDF import: OpenFIGI resolve_isin(%s) took %.2fs -> %s",
            trade.isin,
            time.perf_counter() - t1,
            ticker,
        )
    logger.info(
        "PDF import: ticker resolution total %.2fs (api_key=%s)",
        time.perf_counter() - t0,
        "set" if key else "unset",
    )

    is_duplicate: bool | None = None
    if ticker is not None:
        is_duplicate = await _service.check_is_duplicate(trade, ticker, db)

    return _render(
        request,
        "import_pdf.html",
        {
            "step": "preview_trade",
            "trade": trade,
            "ticker": ticker,
            "is_duplicate": is_duplicate,
        },
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
    broker: Annotated[str, Form()] = "comdirect",
    note: Annotated[str, Form()] = "",
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
        broker=broker.strip() or "comdirect",
        note=note or None,
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


@router.post("/pdf/confirm-batch", response_class=HTMLResponse)
async def import_pdf_confirm_batch(
    request: Request,
    token: Annotated[str, Form()],
    selected: Annotated[list[int], Form()] = [],
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Import the user-selected trades from the batch preview."""
    preview = batch_pdf_cache.get(token)
    if preview is None:
        return _render(
            request,
            "import_pdf.html",
            {"step": "upload", "error": "Preview session expired. Please re-upload your PDFs."},
        )

    created = 0
    duplicates = 0
    errors = 0

    for idx in selected:
        if idx < 0 or idx >= len(preview.items):
            continue
        item = preview.items[idx]

        if item.parse_error:
            errors += 1
            continue

        if item.trade is not None:
            ticker = item.ticker or ""
            if not ticker:
                errors += 1
                continue
            status = await _service.import_trade(item.trade, ticker, db)
            if status == "created":
                created += 1
            elif status == "duplicate":
                duplicates += 1
            else:
                errors += 1
        elif item.pairs is not None:
            processed = await _service.import_from_holdings(item.pairs, db, source_file=item.filename)
            created += len(processed)
        else:
            errors += 1

    batch_pdf_cache.delete(token)

    return _render(
        request,
        "import_pdf.html",
        {"step": "done_batch", "created": created, "duplicates": duplicates, "errors": errors},
    )
