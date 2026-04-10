"""PDF import UI: upload, preview, and confirm."""

from __future__ import annotations

import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session
from app.services.generic_parser import GenericTableParser
from app.services.import_service import ImportService

router = APIRouter(prefix="/import", tags=["import"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)
_parser = GenericTableParser()
_service = ImportService()


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
        pairs = _parser.extract(tmp_path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
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


# ---------------------------------------------------------------------------
# Confirm & commit
# ---------------------------------------------------------------------------


@router.post("/pdf/confirm", response_class=HTMLResponse)
async def import_pdf_confirm(
    request: Request,
    db: AsyncSession = _DB,
    tickers: list[str] = Form(...),
    quantities: list[str] = Form(...),
) -> HTMLResponse:
    """Commit the previewed holdings into the database."""
    pairs: list[tuple[str, Decimal]] = []
    for ticker, qty_str in zip(tickers, quantities):
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
