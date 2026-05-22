"""Portfolio Performance XML import UI — upload, preview, and persist."""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import ParseError

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_async_session
from app.models.stock import ASSET_TYPE_CRYPTO, ASSET_TYPE_STOCK
from app.services import import_cache
from app.services.holdings_service import recompute_holdings
from app.services.portfolio_performance_importer import (
    ParseResult,
    PortfolioPerformanceImporter,
)
from app.services.stock_lookup import fetch_stock_info
from app.services.transaction_import_service import TransactionImportService
from app.services.xml_security_resolver import (
    ResolvedSecurity,
    resolve_securities,
)

router = APIRouter(prefix="/import", tags=["import"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_DB = Depends(get_async_session)
_importer = PortfolioPerformanceImporter()
_transaction_import = TransactionImportService()


def _render(request: Request, name: str, context: dict) -> HTMLResponse:  # type: ignore[type-arg]
    context["request"] = request
    return templates.TemplateResponse(request=request, name=name, context=context)


@router.get("/xml", response_class=HTMLResponse)
async def import_xml_page(request: Request) -> HTMLResponse:
    """Render the XML upload form."""
    return _render(request, "import_xml.html", {"step": "upload"})


@router.post("/xml", response_class=HTMLResponse)
async def import_xml_preview(
    request: Request,
    file: UploadFile,
) -> HTMLResponse:
    """Accept a Portfolio Performance XML (or zip) and show a preview."""
    filename = (file.filename or "").lower()
    if not (filename.endswith(".xml") or filename.endswith(".zip")):
        return _render(
            request,
            "import_xml.html",
            {"step": "upload", "error": "Please upload a .xml or .zip file."},
        )

    contents = await file.read()
    if not contents:
        return _render(
            request,
            "import_xml.html",
            {"step": "upload", "error": "The uploaded file is empty."},
        )

    try:
        result = _importer.parse_bytes(contents)
    except ParseError as exc:
        return _render(
            request,
            "import_xml.html",
            {"step": "upload", "error": f"Invalid XML: {exc}"},
        )
    except ValueError as exc:
        return _render(
            request,
            "import_xml.html",
            {"step": "upload", "error": str(exc)},
        )

    settings = get_settings()
    resolutions = await resolve_securities(
        result.unique_securities, openfigi_api_key=settings.openfigi_api_key
    )

    token = import_cache.store(
        import_cache.ImportPreviewEntry(
            parse_result=result,
            resolutions=resolutions,
            filename=file.filename or "",
        )
    )

    return _render(
        request,
        "import_xml.html",
        _preview_context(result, resolutions, token, file.filename or ""),
    )


@router.post("/xml/confirm", response_class=HTMLResponse)
async def import_xml_confirm(
    request: Request,
    token: str = Form(...),
    db: AsyncSession = _DB,
) -> HTMLResponse:
    """Persist the cached preview using the (possibly user-edited) resolutions."""
    entry = import_cache.get(token)
    if entry is None:
        return _render(
            request,
            "import_xml.html",
            {
                "step": "upload",
                "error": "Your preview expired. Please upload the file again.",
            },
        )

    unresolved = [r for r in entry.resolutions.values() if r.status != "valid"]
    if unresolved:
        return _render(
            request,
            "import_xml.html",
            _preview_context(
                entry.parse_result,
                entry.resolutions,
                token,
                entry.filename,
                error="Resolve every security before confirming the import.",
            ),
        )

    _apply_resolutions(entry.parse_result, entry.resolutions)

    summary = await _transaction_import.import_xml_result(entry.parse_result, db)

    if summary.affected_stock_ids:
        await recompute_holdings(db, summary.affected_stock_ids)

    import_cache.delete(token)

    return _render(
        request,
        "import_xml.html",
        {
            "step": "done",
            "summary": summary,
            "filename": entry.filename,
        },
    )


@router.post("/xml/resolve-row", response_class=HTMLResponse)
async def import_xml_resolve_row(
    request: Request,
    token: str = Form(...),
    uuid: str = Form(...),
    ticker: str = Form(""),
    asset_type: str = Form(ASSET_TYPE_STOCK),
) -> HTMLResponse:
    """Validate a manually-entered ticker and update the cached resolution."""
    entry = import_cache.get(token)
    if entry is None:
        return HTMLResponse(
            '<div class="error">Preview expired — please re-upload the file.</div>',
            status_code=410,
        )

    current = entry.resolutions.get(uuid)
    if current is None:
        return HTMLResponse(status_code=404)

    ticker = ticker.strip().upper()
    asset_type = (asset_type or ASSET_TYPE_STOCK).strip().upper()
    if asset_type not in {ASSET_TYPE_STOCK, ASSET_TYPE_CRYPTO}:
        asset_type = ASSET_TYPE_STOCK

    if not ticker:
        updated = ResolvedSecurity(
            uuid=current.uuid,
            original_ticker=current.original_ticker,
            original_name=current.original_name,
            isin=current.isin,
            status="needs_attention",
            resolved_ticker=None,
            asset_type=asset_type,
            suggestion_source="manual",
            yahoo_name=None,
            currency=current.currency,
        )
    else:
        info = await fetch_stock_info(ticker)
        if info is None:
            updated = ResolvedSecurity(
                uuid=current.uuid,
                original_ticker=current.original_ticker,
                original_name=current.original_name,
                isin=current.isin,
                status="needs_attention",
                resolved_ticker=ticker,
                asset_type=asset_type,
                suggestion_source="manual",
                yahoo_name=None,
                currency=current.currency,
            )
        else:
            updated = ResolvedSecurity(
                uuid=current.uuid,
                original_ticker=current.original_ticker,
                original_name=current.original_name,
                isin=current.isin,
                status="valid",
                resolved_ticker=ticker,
                asset_type=asset_type,
                suggestion_source="manual",
                yahoo_name=info.name,
                currency=info.currency or current.currency,
            )

    import_cache.update_resolution(token, uuid, updated)

    all_valid = all(r.status == "valid" for r in entry.resolutions.values())

    row_html = templates.get_template("partials/security_row.html").render(
        resolution=updated, token=token
    )
    # OOB swap keeps the Confirm button in sync as rows resolve.
    confirm_html = templates.get_template("partials/confirm_button.html").render(
        all_valid=all_valid, token=token, oob=True
    )
    return HTMLResponse(row_html + confirm_html)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _preview_context(
    result: ParseResult,
    resolutions: dict[str, ResolvedSecurity],
    token: str,
    filename: str,
    *,
    error: str | None = None,
) -> dict[str, object]:
    securities = sorted(
        resolutions.values(),
        key=lambda r: (r.status != "needs_attention", (r.original_name or "").lower()),
    )
    all_valid = all(r.status == "valid" for r in resolutions.values())
    return {
        "step": "preview",
        "result": result,
        "filename": filename,
        "token": token,
        "securities": securities,
        "all_valid": all_valid,
        "error": error,
    }


def _apply_resolutions(
    result: ParseResult, resolutions: dict[str, ResolvedSecurity]
) -> None:
    """Push resolved tickers + asset_type back onto SecurityInfo before persist."""
    for sec in result.securities.values():
        resolution = resolutions.get(sec.uuid)
        if resolution is None or resolution.status != "valid":
            continue
        sec.ticker = resolution.resolved_ticker
        sec.asset_type = resolution.asset_type
        if resolution.currency:
            sec.currency = resolution.currency
