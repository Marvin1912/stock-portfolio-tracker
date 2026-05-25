"""PDF text extraction with a fast primary engine and a robust fallback.

pdfplumber builds a full pdfminer object model — every char, line, rect and
curve — for each page. On dense broker statements (comdirect settlements
carry table borders, logos and background fills) that can take tens of
seconds: a real 142 KB comdirect PDF measured at ~40s. pypdf reads the page
content stream directly and returns in well under a second, at the cost of
slightly rougher layout fidelity.

We therefore extract with pypdf first and let the caller fall back to
pdfplumber only when the fast text fails to parse — so the common case is
fast and correctness never regresses.
"""

from __future__ import annotations

from pathlib import Path


def extract_pages_fast(pdf_path: Path) -> list[str]:
    """Return per-page text via pypdf (fast; no graphics object model)."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return [page.extract_text() or "" for page in reader.pages]


def extract_pages_robust(pdf_path: Path) -> list[str]:
    """Return per-page text via pdfplumber (slower; higher layout fidelity)."""
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        return [page.extract_text() or "" for page in pdf.pages]
