"""Parser for comdirect securities settlement PDFs (``Wertpapierabrechnung``).

comdirect statements identify the security by WKN/ISIN — not a ticker — and
carry the full economics of a single trade: share count, price, gross value
(``Kurswert``), fees (``Summe Entgelte``) and the trade date (``Geschäftstag``).
Unlike the simple :class:`~app.services.generic_parser.GenericTableParser`,
which yields ``(ticker, quantity)`` pairs, this parser returns a richer
:class:`ParsedTrade` so the import can persist a complete transaction.

Resolving the WKN/ISIN to a yfinance ticker is intentionally *not* done here
(it needs a network round-trip via OpenFIGI); the router does that at preview
time, mirroring the XML import flow.
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from app.models.transaction import TX_TYPE_BUY, TX_TYPE_SELL

# An ISIN is two country letters, nine alphanumerics, and a check digit.
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
# A German WKN is six uppercase letters/digits.
_WKN_RE = re.compile(r"^[A-Z0-9]{6}$")
# A German-formatted decimal: "1.234,56", "940,32", "117,5406", or "8".
_DE_NUMBER = r"\d{1,3}(?:\.\d{3})*(?:,\d+)?"

# "St. 8 EUR 117,5406" → shares, currency, price-per-share.
_SHARES_RE = re.compile(
    rf"St\.?\s+(?P<shares>{_DE_NUMBER})\s+(?P<ccy>[A-Z]{{3}})\s+(?P<price>{_DE_NUMBER})"
)
# "Kurswert : EUR 940,32" → gross value.
_KURSWERT_RE = re.compile(rf"Kurswert\s*:\s*[A-Z]{{3}}\s+(?P<amount>{_DE_NUMBER})")
# "Summe Entgelte : EUR 15,30" → total fees.
_FEES_RE = re.compile(rf"Summe Entgelte\s*:\s*[A-Z]{{3}}\s+(?P<fee>{_DE_NUMBER})")
# "abgeführte Steuern EUR 0,00" → withheld tax (settlement page 2; absent for buys).
_TAX_RE = re.compile(rf"abgef[üu]hrte Steuern\s+[A-Z]{{3}}\s+(?P<tax>{_DE_NUMBER})")
# "Geschäftstag : 23.03.2026" → trade date.
_DATE_RE = re.compile(r"Gesch[äa]ftstag\s*:\s*(\d{2})\.(\d{2})\.(\d{4})")
# "Ordernummer : 000512215771-001" → stable reference for idempotency.
_ORDER_RE = re.compile(r"Ordernummer\s*:\s*([0-9-]+)")


@dataclass(slots=True)
class ParsedTrade:
    """A single buy/sell extracted from a comdirect settlement PDF."""

    trade_type: str  # TX_TYPE_BUY | TX_TYPE_SELL
    name: str | None
    wkn: str | None
    isin: str | None
    shares: Decimal
    price: Decimal | None
    amount: Decimal  # gross Kurswert
    fee: Decimal
    tax: Decimal
    currency: str
    date: datetime.datetime
    order_ref: str | None

    @property
    def display(self) -> str:
        parts = [p for p in (self.name, self.wkn, self.isin) if p]
        return " · ".join(parts) if parts else "Unknown security"


def _de_decimal(raw: str) -> Decimal:
    """Convert a German-formatted number ("1.234,56") to :class:`Decimal`."""
    normalised = raw.strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(normalised)
    except InvalidOperation as exc:  # pragma: no cover - defensive
        raise ValueError(f"Cannot parse number {raw!r}") from exc


class ComdirectParser:
    """Extract a single trade from a comdirect ``Wertpapierabrechnung`` PDF."""

    @staticmethod
    def matches(text: str) -> bool:
        """Return True if *text* looks like a comdirect securities settlement."""
        lowered = text.lower()
        if "comdirect" not in lowered:
            return False
        return "wertpapierkauf" in lowered or "wertpapierverkauf" in lowered

    def extract_trade(self, pdf_path: Path) -> ParsedTrade | None:
        """Read *pdf_path* and parse the contained trade, or None if absent."""
        with pdfplumber.open(pdf_path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n".join(pages)
        return self.parse_text(text)

    def parse_text(self, text: str) -> ParsedTrade | None:
        """Parse the full document text into a :class:`ParsedTrade`.

        Returns None when the mandatory fields (security identifier, shares
        and gross value) cannot be located, so the caller can fall back to
        another parser.
        """
        if not self.matches(text):
            return None

        lines = [line.strip() for line in text.splitlines()]
        lowered_lines = [line.lower() for line in lines]

        trade_type = TX_TYPE_BUY
        if any("wertpapierverkauf" in line for line in lowered_lines):
            trade_type = TX_TYPE_SELL

        shares_m = _SHARES_RE.search(text)
        kurswert_m = _KURSWERT_RE.search(text)
        if shares_m is None or kurswert_m is None:
            return None

        shares = _de_decimal(shares_m.group("shares"))
        currency = shares_m.group("ccy")
        price = _de_decimal(shares_m.group("price"))
        amount = _de_decimal(kurswert_m.group("amount"))

        fees_m = _FEES_RE.search(text)
        fee = _de_decimal(fees_m.group("fee")) if fees_m else Decimal("0")

        tax_m = _TAX_RE.search(text)
        tax = _de_decimal(tax_m.group("tax")) if tax_m else Decimal("0")

        name, wkn, isin = self._extract_security(lines, lowered_lines)
        if wkn is None and isin is None:
            return None

        order_m = _ORDER_RE.search(text)
        order_ref = order_m.group(1) if order_m else None

        return ParsedTrade(
            trade_type=trade_type,
            name=name,
            wkn=wkn,
            isin=isin,
            shares=shares,
            price=price,
            amount=amount,
            fee=fee,
            tax=tax,
            currency=currency,
            date=self._extract_date(text),
            order_ref=order_ref,
        )

    @staticmethod
    def _extract_security(
        lines: list[str], lowered_lines: list[str]
    ) -> tuple[str | None, str | None, str | None]:
        """Pull name, WKN and ISIN from the ``Wertpapier-Bezeichnung`` block.

        The block sits between the ``WPKNR/ISIN`` header and the ``Nennwert``
        row, e.g.::

            Xtr.(IE) - MSCI World        A1XB5U
            Registered Shares 1C o.N.    IE00BJ0KDQ92
        """
        start = next(
            (i for i, line in enumerate(lowered_lines) if "wpknr/isin" in line),
            None,
        )
        end = next(
            (i for i, line in enumerate(lowered_lines) if line.startswith("nennwert")),
            None,
        )
        if start is None:
            return None, None, None
        block = lines[start + 1 : end if end is not None else start + 4]

        wkn: str | None = None
        isin: str | None = None
        name_parts: list[str] = []

        for line in block:
            tokens = line.split()
            if not tokens:
                continue
            # The identifier — ISIN or WKN — is the rightmost token; everything
            # before it is part of the security name.
            last = tokens[-1]
            if _ISIN_RE.match(last):
                isin = last
                tokens = tokens[:-1]
            elif wkn is None and _WKN_RE.match(last):
                wkn = last
                tokens = tokens[:-1]
            if tokens:
                name_parts.append(" ".join(tokens))

        name = " ".join(name_parts).strip() or None
        return name, wkn, isin

    @staticmethod
    def _extract_date(text: str) -> datetime.datetime:
        """Parse the ``Geschäftstag`` as a UTC datetime, defaulting to now."""
        m = _DATE_RE.search(text)
        if m is None:
            return datetime.datetime.now(datetime.UTC)
        day, month, year = (int(g) for g in m.groups())
        return datetime.datetime(year, month, day, tzinfo=datetime.UTC)
