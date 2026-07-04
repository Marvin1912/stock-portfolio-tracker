#!/usr/bin/env python3
"""Generate a comdirect-style ``Dividendengutschrift`` PDF for use as a test fixture.

The layout mirrors a real comdirect dividend credit note; umlauts are spelled
with plain ASCII so the minimal hand-rolled Helvetica font renders
deterministically under pdfplumber.

Run from the repo root:
    python tests/fixtures/generate_comdirect_dividend_pdf.py
"""

from __future__ import annotations

from pathlib import Path

_LINES_USD_DIVIDEND = [
    "comdirect bank – Dividendengutschrift",
    "Referenz-Nr. : 1AINA2WQGJM0064Z",
    "Geschaeftstag : 01.03.2026",
    "per 15.03.2026 STRYKER CORP. 864765",
    "STK 10 US8863161029 STRYKER CORP.",
    "Depotbestand : 10",
    "Quartalsdividende Bruttobetrag USD 1,00",
    "Devisenkurs : 1,0950",
    "Bruttobetrag EUR 10,95",
    "Quellensteuer USD 0,15 (15%) = EUR 0,16",
    "Nettobetrag EUR 10,79",
    "Verrechnung ueber Konto Nr. ... 10,79",
    "Valuta: 15.03.2026",
    "zahlbar ab 15.03.2026 Quartalsdividende",
]

_LINES_EUR_DIVIDEND = [
    "comdirect bank – Dividendengutschrift",
    "Referenz-Nr. : 2BJNA2WQGJM0065A",
    "Geschaeftstag : 01.06.2026",
    "per 15.06.2026 DEUTSCHE TELEKOM 555750",
    "STK 20 DE0005557508 DEUTSCHE TELEKOM",
    "Depotbestand : 20",
    "Halbjahresdividende Bruttobetrag EUR 0,70",
    "Bruttobetrag EUR 14,00",
    "Nettobetrag EUR 14,00",
    "Verrechnung ueber Konto Nr. ... 14,00",
    "Valuta: 15.06.2026",
    "zahlbar ab 15.06.2026 Halbjahresdividende",
]


def create_dividend_pdf(output_path: Path, lines: list[str]) -> None:
    """Write a minimal but valid PDF containing the dividend statement text."""
    ops: list[str] = ["BT", "/F1 10 Tf", "50 770 Td"]
    for i, line in enumerate(lines):
        if i > 0:
            ops.append("0 -15 Td")
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({escaped}) Tj")
    ops.append("ET")
    content: bytes = ("\n".join(ops) + "\n").encode()

    body = b"%PDF-1.4\n"
    offsets: list[int] = []

    def add(obj_bytes: bytes) -> None:
        nonlocal body
        offsets.append(len(body))
        body += obj_bytes

    add(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    add(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    add(
        b"3 0 obj\n"
        b"<< /Type /Page /Parent 2 0 R "
        b"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> >> >> "
        b"/MediaBox [0 0 612 792] /Contents 4 0 R >>\n"
        b"endobj\n"
    )
    stream_header = f"4 0 obj\n<< /Length {len(content)} >>\nstream\n".encode()
    add(stream_header + content + b"endstream\nendobj\n")

    xref_offset = len(body)
    xref = b"xref\n0 5\n0000000000 65535 f \n"
    for off in offsets:
        xref += f"{off:010d} 00000 n \n".encode()

    trailer = (
        f"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    ).encode()

    output_path.write_bytes(body + xref + trailer)
    print(f"Written: {output_path}")


if __name__ == "__main__":
    usd_dest = Path(__file__).parent / "sample_comdirect_dividend_usd.pdf"
    create_dividend_pdf(usd_dest, _LINES_USD_DIVIDEND)

    eur_dest = Path(__file__).parent / "sample_comdirect_dividend_eur.pdf"
    create_dividend_pdf(eur_dest, _LINES_EUR_DIVIDEND)
