"""Parser for ING-DiBa securities settlement PDFs (``Wertpapierabrechnung``).

ING trade confirmations carry the same economics as a comdirect settlement —
security WKN/ISIN, share count, price, gross value (``Kurswert``), commission
(``Provision``), the execution date and a stable order number — so this parser
reuses the richer :class:`~app.services.comdirect_parser.ParsedTrade` model (and
hence the whole preview/confirm/dedupe pipeline). Only the document layout and
the German labels differ; see the sample extracted text below.

Resolving the WKN/ISIN to a yfinance ticker is intentionally *not* done here
(it needs a network round-trip via OpenFIGI); the router does that at preview
time, mirroring the comdirect flow.

The pypdf-extracted text looks like::

    Wertpapierabrechnung Kauf
    Ordernummer 456480204.001
    ISIN (WKN) IE00B4L5Y983 (A0RPWH)
    Wertpapierbezeichnung iShsIII-Core MSCI World U.ETF
    Registered Shs USD (Acc) o.N.
    Nominale Stück 9,00
    Kurs EUR 107,5157
    Ausführungstag / -zeit 23.03.2026 um 07:33:17 Uhr
    Kurswert EUR 967,64
    Provision EUR 7,32
    Endbetrag zu Ihren Lasten EUR 974,96
"""

from __future__ import annotations

import datetime
import logging
import re
from decimal import Decimal
from pathlib import Path

from app.models.transaction import TX_TYPE_BUY, TX_TYPE_SELL
from app.services.comdirect_parser import ParsedTrade, _de_decimal
from app.services.pdf_text import extract_pages_fast, extract_pages_robust

logger = logging.getLogger(__name__)

# A German-formatted decimal: "1.234,56", "967,64", "107,5157", or "9".
_DE_NUMBER = r"\d{1,3}(?:\.\d{3})*(?:,\d+)?"

# "Ordernummer 456480204.001" → stable reference for idempotency.
_ORDER_RE = re.compile(r"Ordernummer\s+([0-9][0-9.\-]*)")
# "ISIN (WKN) IE00B4L5Y983 (A0RPWH)" → ISIN + WKN in one go.
_ISIN_WKN_RE = re.compile(r"([A-Z]{2}[A-Z0-9]{9}[0-9])\s*\(([A-Z0-9]{6})\)")
# "Nominale Stück 9,00" → share count ("ue" covers ASCII-transliterated PDFs).
_SHARES_RE = re.compile(rf"St(?:ü|ue|u)ck\s+(?P<shares>{_DE_NUMBER})")
# "Kurs EUR 107,5157" → currency + price-per-share.
_PRICE_RE = re.compile(rf"Kurs\s+(?P<ccy>[A-Z]{{3}})\s+(?P<price>{_DE_NUMBER})")
# "Kurswert EUR 967,64" → gross value.
_KURSWERT_RE = re.compile(rf"Kurswert\s+[A-Z]{{3}}\s+(?P<amount>{_DE_NUMBER})")
# "Provision EUR 7,32" → commission/fees.
_FEES_RE = re.compile(rf"Provision\s+[A-Z]{{3}}\s+(?P<fee>{_DE_NUMBER})")
# "Ausführungstag / -zeit 23.03.2026 um 07:33:17 Uhr" → execution date.
_DATE_RE = re.compile(r"Ausf(?:ü|ue|u)hrungstag[^\n]*?(\d{2})\.(\d{2})\.(\d{4})")


class IngParser:
    """Extract a single trade from an ING-DiBa ``Wertpapierabrechnung`` PDF."""

    @staticmethod
    def matches(text: str) -> bool:
        """Return True if *text* looks like an ING securities settlement."""
        lowered = text.lower()
        return "ing-diba" in lowered and "wertpapierabrechnung" in lowered

    def extract_trade(self, pdf_path: Path) -> ParsedTrade | None:
        """Read *pdf_path* and parse the contained trade, or None if absent.

        Uses pypdf first (fast) and only falls back to the much slower
        pdfplumber when the document *looks* like an ING statement but the fast
        text didn't yield the required fields — mirroring
        :meth:`app.services.comdirect_parser.ComdirectParser.extract_trade`.
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
            return None  # definitively not an ING doc — skip slow retry.

        text = "\n".join(extract_pages_robust(pdf_path))
        return self.parse_text(text)

    def parse_text(self, text: str) -> ParsedTrade | None:
        """Parse the full document text into a :class:`ParsedTrade`.

        Returns None when the mandatory fields (security identifier, shares and
        gross value) cannot be located, so the caller can fall back to another
        parser.
        """
        if not self.matches(text):
            return None

        lowered = text.lower()
        trade_type = TX_TYPE_BUY
        if "verkauf" in lowered or "zu ihren gunsten" in lowered:
            trade_type = TX_TYPE_SELL

        shares_m = _SHARES_RE.search(text)
        kurswert_m = _KURSWERT_RE.search(text)
        ident_m = _ISIN_WKN_RE.search(text)
        if shares_m is None or kurswert_m is None or ident_m is None:
            return None

        shares = _de_decimal(shares_m.group("shares"))
        amount = _de_decimal(kurswert_m.group("amount"))
        isin, wkn = ident_m.group(1), ident_m.group(2)

        price_m = _PRICE_RE.search(text)
        price = _de_decimal(price_m.group("price")) if price_m else None
        currency = price_m.group("ccy") if price_m else "EUR"

        fees_m = _FEES_RE.search(text)
        fee = _de_decimal(fees_m.group("fee")) if fees_m else Decimal("0")

        order_m = _ORDER_RE.search(text)
        order_ref = order_m.group(1) if order_m else None

        return ParsedTrade(
            trade_type=trade_type,
            name=self._extract_name(text),
            wkn=wkn,
            isin=isin,
            shares=shares,
            price=price,
            amount=amount,
            fee=fee,
            # ING buy confirmations carry no withheld tax; sell-side tax
            # breakdown is not yet modelled (no sample available).
            tax=Decimal("0"),
            currency=currency,
            date=self._extract_date(text),
            order_ref=order_ref,
            broker="ing",
        )

    @staticmethod
    def _extract_name(text: str) -> str | None:
        """Pull the security name from the ``Wertpapierbezeichnung`` block.

        The name starts on the ``Wertpapierbezeichnung`` line (after the label)
        and may continue on the following line(s) until the ``Nominale`` row::

            Wertpapierbezeichnung iShsIII-Core MSCI World U.ETF
            Registered Shs USD (Acc) o.N.
            Nominale Stück 9,00
        """
        lines = [line.strip() for line in text.splitlines()]
        start = next(
            (i for i, line in enumerate(lines) if line.startswith("Wertpapierbezeichnung")),
            None,
        )
        if start is None:
            return None
        parts = [lines[start].removeprefix("Wertpapierbezeichnung").strip()]
        for line in lines[start + 1 :]:
            if not line or line.startswith("Nominale"):
                break
            parts.append(line)
        name = " ".join(p for p in parts if p).strip()
        return name or None

    @staticmethod
    def _extract_date(text: str) -> datetime.datetime:
        """Parse the ``Ausführungstag`` as a UTC datetime, defaulting to now.

        The time-of-day ING prints is intentionally dropped so the date-only
        value matches comdirect's convention and the fuzzy same-day dedupe.
        """
        m = _DATE_RE.search(text)
        if m is None:
            return datetime.datetime.now(datetime.UTC)
        day, month, year = (int(g) for g in m.groups())
        return datetime.datetime(year, month, day, tzinfo=datetime.UTC)
