"""Parser for comdirect Dividendengutschrift (dividend credit note) PDFs.

comdirect dividend statements (``Dividendengutschrift``) carry the security
identifier (WKN/ISIN), quantity of shares held on the dividend date, the gross
and net dividend amount (in EUR), any withheld tax, and a stable reference
number. This parser returns a :class:`ParsedTrade` with ``trade_type=DIVIDEND``
so the import can persist a complete transaction via the same preview/confirm
pipeline as buy/sell settlements.

Resolving the WKN/ISIN to a yfinance ticker is intentionally *not* done here
(it needs a network round-trip via OpenFIGI); the router does that at preview
time, mirroring the comdirect_parser.py and XML import flow.
"""

from __future__ import annotations

import datetime
import logging
import re
from decimal import Decimal
from pathlib import Path

from app.models.transaction import TX_TYPE_DIVIDEND
from app.services.comdirect_parser import ParsedTrade, _de_decimal
from app.services.pdf_text import extract_pages_fast, extract_pages_robust

logger = logging.getLogger(__name__)

# An ISIN is two country letters, nine alphanumerics, and a check digit.
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
# A German WKN is six uppercase letters/digits.
_WKN_RE = re.compile(r"^[A-Z0-9]{6}$")
# A German-formatted decimal: "1.234,56", "940,32", "117,5406", or "8".
_DE_NUMBER = r"\d{1,3}(?:\.\d{3})*(?:,\d+)?"

# "Referenz-Nr. : 1AINA2WQGJM0064Z" → stable reference for idempotency.
_REF_RE = re.compile(r"Referenz-Nr\.\s*:\s*([A-Z0-9]+)")
# "Depotbestand : 10" or "Depotbestand 10" → shares held on dividend date.
_SHARES_RE = re.compile(rf"Depotbestand\s*:?\s*(?P<shares>{_DE_NUMBER})")
# "Verrechnung über Konto ... EUR 123,45" → net amount (EUR).
_AMOUNT_RE = re.compile(
    rf"Verrechnung\s+(?:ü|ue)ber\s+Konto[^\n]*?(?P<amount>{_DE_NUMBER})\s*$", re.MULTILINE
)
# "Quellensteuer USD 0,15 ..." or "Quellensteuer EUR 10,00" → withheld tax (currency and amount).
_TAX_RE = re.compile(rf"Quellensteuer\s+(?P<ccy>[A-Z]{{3}})\s+(?P<tax>{_DE_NUMBER})", re.MULTILINE)
# "Devisenkurs ... 1,0950" → FX rate (when tax is in non-EUR currency).
_FX_RATE_RE = re.compile(rf"Devisenkurs\s*:\s*(?P<rate>{_DE_NUMBER})")
# "Valuta: 12.03.2026" or "Valuta 12.03.2026" → settlement date.
_VALUTA_RE = re.compile(r"Valuta\s*:?\s*(\d{2})\.(\d{2})\.(\d{4})")
# Extract the descriptor (e.g., "Quartalsdividende") after "zahlbar ab <date>".
_DESCRIPTOR_RE = re.compile(r"zahlbar\s+ab\s+\d{2}\.\d{2}\.\d{4}\s+(.+?)(?:\s*$|$)", re.MULTILINE)


class ComdirectDividendParser:
    """Extract a single dividend from a comdirect ``Dividendengutschrift`` PDF."""

    @staticmethod
    def matches(text: str) -> bool:
        """Return True if *text* looks like a comdirect dividend statement."""
        lowered = text.lower()
        if "comdirect" not in lowered:
            return False
        return "dividendengutschrift" in lowered

    def extract_trade(self, pdf_path: Path) -> ParsedTrade | None:
        """Read *pdf_path* and parse the contained dividend, or None if absent.

        Extraction uses pypdf first (fast); pdfplumber — which is an order of
        magnitude slower on dense PDFs — is used only as a fallback when the
        document *looks* like a comdirect dividend statement but the fast text
        didn't yield the required fields. Documents that aren't comdirect at
        all never pay the slow path.
        """
        try:
            text = "\n".join(extract_pages_fast(pdf_path))
        except Exception:  # noqa: BLE001 - pypdf failed; let pdfplumber try.
            logger.warning("pypdf extraction failed; falling back to pdfplumber")
            text = ""

        trade = self.parse_text(text) if text else None
        if trade is not None:
            return trade
        if text and not self.matches(text):
            return None  # definitively not a comdirect doc — skip slow retry.

        text = "\n".join(extract_pages_robust(pdf_path))
        return self.parse_text(text)

    def parse_text(self, text: str) -> ParsedTrade | None:
        """Parse the full document text into a :class:`ParsedTrade`.

        Returns None when mandatory fields (security identifier, amount) cannot
        be located, so the caller can fall back to another parser.
        """
        if not self.matches(text):
            return None

        # Extract mandatory fields.
        amount_m = _AMOUNT_RE.search(text)
        if amount_m is None:
            return None
        amount = _de_decimal(amount_m.group("amount"))

        # Extract shares (Depotbestand) — used for display but doesn't affect position.
        shares_m = _SHARES_RE.search(text)
        shares = _de_decimal(shares_m.group("shares")) if shares_m else Decimal("0")

        # Extract tax (Quellensteuer) and FX rate if needed.
        tax = Decimal("0")
        tax_m = _TAX_RE.search(text)
        if tax_m:
            tax_raw = _de_decimal(tax_m.group("tax"))
            tax_ccy = tax_m.group("ccy")
            # Check if we need to convert tax from a non-EUR currency.
            if tax_ccy != "EUR":
                fx_m = _FX_RATE_RE.search(text)
                if fx_m:
                    fx_rate = _de_decimal(fx_m.group("rate"))
                    tax = tax_raw * fx_rate
                else:
                    tax = tax_raw
            else:
                tax = tax_raw

        # Extract security identifiers (WKN/ISIN/name).
        name, wkn, isin = self._extract_security(text)
        if wkn is None and isin is None:
            return None

        # Extract order reference.
        ref_m = _REF_RE.search(text)
        order_ref = ref_m.group(1) if ref_m else None

        # Extract valuta date.
        date = self._extract_valuta_date(text)

        # Extract descriptor for the note.
        descriptor = self._extract_descriptor(text)
        note = None
        if order_ref and descriptor:
            note = f"Ref.-Nr.: {order_ref} | {descriptor}"
        elif order_ref:
            note = f"Ref.-Nr.: {order_ref}"

        return ParsedTrade(
            trade_type=TX_TYPE_DIVIDEND,
            name=name,
            wkn=wkn,
            isin=isin,
            shares=shares,
            price=None,
            amount=amount,
            fee=Decimal("0"),
            tax=tax,
            currency="EUR",
            date=date,
            order_ref=order_ref,
            broker="comdirect",
            note=note,
        )

    @staticmethod
    def _extract_security(text: str) -> tuple[str | None, str | None, str | None]:
        """Extract security name, WKN and ISIN from the dividend statement.

        The statement lists the security as:
            per <date> <WKN/ISIN> <name>
            STK <qty> <ISIN> <name...>

        Unlike buy/sell settlement statements, the identifier is glued to the
        *front* of the name here.
        """
        lines = [line.strip() for line in text.splitlines()]

        wkn: str | None = None
        isin: str | None = None
        name_parts: list[str] = []

        # Look for lines starting with "per " or "STK " that contain identifiers.
        for line in lines:
            if line.startswith("per ") or line.startswith("STK "):
                tokens = line.split()
                if len(tokens) > 1:
                    # The first substantial token after "per"/"STK" and a date/qty
                    # should be the identifier (ISIN or WKN).
                    for i, token in enumerate(tokens[1:], start=1):
                        if _ISIN_RE.match(token):
                            isin = token
                            # Everything after ISIN is the name.
                            name_parts.extend(tokens[i + 1 :])
                            break
                        elif _WKN_RE.match(token):
                            wkn = token
                            # Everything after WKN is the name.
                            name_parts.extend(tokens[i + 1 :])
                            break

        name = " ".join(name_parts).strip() or None
        return name, wkn, isin

    @staticmethod
    def _extract_valuta_date(text: str) -> datetime.datetime:
        """Parse the ``Valuta`` date as a UTC datetime, defaulting to now."""
        m = _VALUTA_RE.search(text)
        if m is None:
            return datetime.datetime.now(datetime.UTC)
        day, month, year = (int(g) for g in m.groups())
        return datetime.datetime(year, month, day, tzinfo=datetime.UTC)

    @staticmethod
    def _extract_descriptor(text: str) -> str | None:
        """Extract the dividend descriptor (e.g., 'Quartalsdividende') from the text.

        Looks for text after 'zahlbar ab <date>'.
        """
        m = _DESCRIPTOR_RE.search(text)
        if m is None:
            return None
        return m.group(1).strip() or None
