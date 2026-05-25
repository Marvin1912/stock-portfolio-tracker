"""Generic tabular broker PDF parser."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

from app.services.pdf_parser import BaseBrokerParser
from app.services.pdf_text import extract_pages_fast, extract_pages_robust

# Matches lines like "AAPL 10.00000000" – a single all-caps ticker followed by
# a decimal (or integer) quantity.
_ROW_RE = re.compile(r"^([A-Z]{1,10})\s+([\d]+(?:\.[\d]+)?)$")


class GenericTableParser(BaseBrokerParser):
    """Parser for broker PDFs that contain a plain text table.

    Each data row must have the format::

        <TICKER>   <QUANTITY>

    where *TICKER* is one-to-ten uppercase letters and *QUANTITY* is a decimal
    number.  Header rows and free-text lines are ignored automatically.
    """

    def extract(self, pdf_path: Path) -> list[tuple[str, Decimal]]:
        # pypdf is fast but lower-fidelity on tables; if it finds no rows we
        # retry with pdfplumber before giving up. The previewed rows are shown
        # for confirmation before import, so a fast-path miss is recoverable.
        try:
            results = self._parse_pages(extract_pages_fast(pdf_path))
        except Exception:  # noqa: BLE001 - pypdf failed; let pdfplumber try.
            results = []
        if results:
            return results
        return self._parse_pages(extract_pages_robust(pdf_path))

    @staticmethod
    def _parse_pages(pages: list[str]) -> list[tuple[str, Decimal]]:
        results: list[tuple[str, Decimal]] = []
        for text in pages:
            for line in text.splitlines():
                m = _ROW_RE.match(line.strip())
                if m:
                    results.append((m.group(1).upper(), Decimal(m.group(2))))
        return results
