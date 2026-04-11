"""Generic tabular broker PDF parser."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pdfplumber

from app.services.pdf_parser import BaseBrokerParser

# Matches lines like "865985 10.00000000" – a 6-character alphanumeric WKN followed
# by a decimal (or integer) quantity.
_ROW_RE = re.compile(r"^([A-Z0-9]{6})\s+([\d]+(?:\.[\d]+)?)$")


class GenericTableParser(BaseBrokerParser):
    """Parser for broker PDFs that contain a plain text table.

    Each data row must have the format::

        <WKN>   <QUANTITY>

    where *WKN* is exactly six alphanumeric characters and *QUANTITY* is a decimal
    number.  Header rows and free-text lines are ignored automatically.
    """

    def extract(self, pdf_path: Path) -> list[tuple[str, Decimal]]:
        results: list[tuple[str, Decimal]] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text() or ""
                for line in text.splitlines():
                    m = _ROW_RE.match(line.strip())
                    if m:
                        wkn = m.group(1).upper()
                        quantity = Decimal(m.group(2))
                        results.append((wkn, quantity))
        return results
