"""Portfolio Performance XML import UI — upload and preview."""

from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import ParseError

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.portfolio_performance_importer import PortfolioPerformanceImporter

router = APIRouter(prefix="/import", tags=["import"])

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

_importer = PortfolioPerformanceImporter()


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

    return _render(
        request,
        "import_xml.html",
        {
            "step": "preview",
            "result": result,
            "filename": file.filename,
        },
    )
