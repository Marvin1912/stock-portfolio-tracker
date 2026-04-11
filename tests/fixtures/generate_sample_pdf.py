#!/usr/bin/env python3
"""Generate an anonymised sample broker PDF for use as a test fixture.

Run from the repo root:
    python tests/fixtures/generate_sample_pdf.py
"""

from __future__ import annotations

from pathlib import Path


def create_broker_pdf(output_path: Path) -> None:
    """Write a minimal but valid PDF containing a plain-text holdings table."""
    lines = [
        "BROKER PORTFOLIO STATEMENT 2024-01-15",
        "",
        "WKN             Quantity",
        "865985          10.00000000",
        "870747          5.50000000",
        "A14Y6F          2.75000000",
    ]

    ops: list[str] = ["BT", "/F1 10 Tf", "50 750 Td"]
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
    dest = Path(__file__).parent / "sample_holdings.pdf"
    create_broker_pdf(dest)
