#!/usr/bin/env python3
"""Generate an ING-DiBa-style ``Wertpapierabrechnung`` PDF test fixture.

The layout mirrors a real ING securities settlement (the lines the IngParser
keys on); umlauts are spelled with plain ASCII ("ue") so the minimal
hand-rolled Helvetica font renders deterministically under pdfplumber — the
parser's regexes accept both the umlaut and the ASCII spelling.

Run from the repo root:
    python tests/fixtures/generate_ing_pdf.py
"""

from __future__ import annotations

from pathlib import Path

_LINES = [
    "Wertpapierabrechnung Kauf",
    "Ordernummer 456480204.001",
    "ISIN (WKN) IE00B4L5Y983 (A0RPWH)",
    "Wertpapierbezeichnung iShsIII-Core MSCI World U.ETF",
    "Registered Shs USD (Acc) o.N.",
    "Nominale Stueck 9,00",
    "Kurs EUR 107,5157",
    "Handelsplatz Direkthandel",
    "Ausfuehrungstag / -zeit 23.03.2026 um 07:33:17 Uhr",
    "Kurswert EUR 967,64",
    "Provision EUR 7,32",
    "Endbetrag zu Ihren Lasten EUR 974,96",
    "Valuta 25.03.2026",
    "ING-DiBa AG - 60628 Frankfurt am Main",
]


def create_ing_pdf(output_path: Path) -> None:
    """Write a minimal but valid PDF containing the ING statement text."""
    ops: list[str] = ["BT", "/F1 10 Tf", "50 770 Td"]
    for i, line in enumerate(_LINES):
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
    dest = Path(__file__).parent / "sample_ing_kauf.pdf"
    create_ing_pdf(dest)
