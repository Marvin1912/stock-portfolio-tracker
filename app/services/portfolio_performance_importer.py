"""Portfolio Performance XML file parser.

Parses the XStream-serialised XML produced by Portfolio Performance and
returns a structured preview of the contained transactions.  The parser
does not touch the database — it is a pure extraction layer that the
import router uses to drive the preview UI.

XML structure is documented in issue #82.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Data classes used by the preview layer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class SecurityInfo:
    uuid: str
    name: str | None = None
    isin: str | None = None
    ticker: str | None = None
    currency: str | None = None

    @property
    def display(self) -> str:
        parts = [p for p in (self.name, self.ticker, self.isin) if p]
        return " · ".join(parts) if parts else self.uuid


@dataclass(slots=True)
class Unit:
    type: str  # FEE | TAX | GROSS_VALUE
    amount: Decimal
    currency: str


@dataclass(slots=True)
class ParsedTransaction:
    kind: Literal["portfolio", "account"]
    uuid: str
    date: datetime
    type: str
    amount: Decimal
    currency: str
    shares: Decimal
    note: str | None
    security: SecurityInfo | None
    units: list[Unit] = field(default_factory=list)

    @property
    def fees(self) -> Decimal:
        return sum((u.amount for u in self.units if u.type == "FEE"), Decimal("0"))

    @property
    def taxes(self) -> Decimal:
        return sum((u.amount for u in self.units if u.type == "TAX"), Decimal("0"))


@dataclass(slots=True)
class TypeBreakdown:
    type: str
    count: int


@dataclass(slots=True)
class ParseResult:
    version: str | None
    base_currency: str | None
    transactions: list[ParsedTransaction]
    securities: dict[str, SecurityInfo]
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Convenience summaries used by the preview template
    # ------------------------------------------------------------------
    @property
    def total_count(self) -> int:
        return len(self.transactions)

    @property
    def type_breakdown(self) -> list[TypeBreakdown]:
        counts: dict[str, int] = {}
        for t in self.transactions:
            counts[t.type] = counts.get(t.type, 0) + 1
        return [
            TypeBreakdown(type=k, count=v)
            for k, v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        ]

    @property
    def date_range(self) -> tuple[datetime, datetime] | None:
        if not self.transactions:
            return None
        dates = [t.date for t in self.transactions]
        return min(dates), max(dates)

    @property
    def unique_securities(self) -> list[SecurityInfo]:
        seen: dict[str, SecurityInfo] = {}
        for t in self.transactions:
            if t.security and t.security.uuid not in seen:
                seen[t.security.uuid] = t.security
        return list(seen.values())


# ---------------------------------------------------------------------------
# Parser implementation
# ---------------------------------------------------------------------------

_AMOUNT_FACTOR = Decimal(100)           # PP stores monetary amounts as cents (precision=2)
_SHARE_FACTOR = Decimal(100_000_000)    # PP stores share quantities with precision=8


class PortfolioPerformanceImporter:
    """Parses Portfolio Performance XML (optionally inside a zip).

    The importer follows the ``XPATH_RELATIVE_REFERENCES`` convention used
    by Portfolio Performance.  ``<security reference="../../../securities/security"/>``
    means: walk up the given number of path segments from the current
    element, then descend into the remaining segments.  We also honour
    positional indices such as ``security[2]`` (1-based).
    """

    def parse_bytes(self, data: bytes) -> ParseResult:
        xml_bytes = self._maybe_unzip(data)
        root = ET.fromstring(xml_bytes)
        return self._parse_root(root)

    def parse_file(self, path: Path) -> ParseResult:
        return self.parse_bytes(path.read_bytes())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _maybe_unzip(data: bytes) -> bytes:
        if data[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
                if not xml_names:
                    raise ValueError("Zip archive does not contain an XML file.")
                with zf.open(xml_names[0]) as fh:
                    return fh.read()
        return data

    def _parse_root(self, root: ET.Element) -> ParseResult:
        version = _text(root.find("version"))
        base_currency = _text(root.find("baseCurrency"))

        securities = self._collect_securities(root)
        warnings: list[str] = []

        transactions = self._extract_all_transactions(root, securities, warnings)
        transactions.sort(key=lambda t: t.date)

        return ParseResult(
            version=version,
            base_currency=base_currency,
            transactions=transactions,
            securities=securities,
            warnings=warnings,
        )

    # -- securities -----------------------------------------------------

    def _collect_securities(self, root: ET.Element) -> dict[str, SecurityInfo]:
        securities: dict[str, SecurityInfo] = {}
        sec_container = root.find("securities")
        if sec_container is None:
            return securities
        for sec in sec_container.findall("security"):
            uuid = _text(sec.find("uuid"))
            if not uuid:
                continue
            securities[uuid] = SecurityInfo(
                uuid=uuid,
                name=_text(sec.find("name")),
                isin=_text(sec.find("isin")),
                ticker=_text(sec.find("tickerSymbol")),
                currency=_text(sec.find("currencyCode")),
            )
        return securities

    # -- transaction extraction ----------------------------------------

    def _extract_all_transactions(
        self,
        root: ET.Element,
        securities: dict[str, SecurityInfo],
        warnings: list[str],
    ) -> list[ParsedTransaction]:
        """Recursively find every transaction element in the document.

        Portfolio Performance serialises accounts before portfolios in the XML.
        The first BUY/SELL account-transaction triggers inline serialisation of
        the entire linked Portfolio (with all its portfolio-transactions) inside
        the <crossEntry> element.  Subsequent references to those objects are
        written as <... reference="..."/> stubs with no content.

        Walking just `portfolios/portfolio/transactions` therefore misses every
        BUY/SELL portfolio-transaction.  Instead we iterate the full element
        tree and skip back-reference stubs (elements that carry a ``reference``
        attribute but no UUID child).
        """
        # Build parent map once so we can reconstruct each element's XPath.
        parent_map: dict[ET.Element, ET.Element] = {
            child: parent for parent in root.iter() for child in parent
        }

        def compute_path(element: ET.Element) -> list[str]:
            segments: list[str] = []
            current = element
            while current in parent_map:
                p = parent_map[current]
                same_tag = [c for c in p if c.tag == current.tag]
                idx = same_tag.index(current) + 1
                segments.append(
                    f"{current.tag}[{idx}]" if len(same_tag) > 1 else current.tag
                )
                current = p
            segments.append(root.tag)
            return list(reversed(segments))

        out: list[ParsedTransaction] = []
        seen: set[str] = set()

        for tag, kind in (
            ("portfolio-transaction", "portfolio"),
            ("account-transaction", "account"),
        ):
            for tx_el in root.iter(tag):
                if tx_el.get("reference"):
                    continue  # back-reference stub — actual data is elsewhere
                uuid = _text(tx_el.find("uuid")) or ""
                if uuid in seen:
                    continue
                seen.add(uuid)
                path = compute_path(tx_el)
                parsed = self._parse_transaction(
                    tx_el, root, path, kind, securities, warnings  # type: ignore[arg-type]
                )
                if parsed:
                    out.append(parsed)

        return out

    def _parse_transaction(
        self,
        tx_el: ET.Element,
        root: ET.Element,
        tx_path: list[str],
        kind: Literal["portfolio", "account"],
        securities: dict[str, SecurityInfo],
        warnings: list[str],
    ) -> ParsedTransaction | None:
        uuid = _text(tx_el.find("uuid")) or ""
        tx_type = _text(tx_el.find("type")) or "UNKNOWN"

        date_el = tx_el.find("date")
        date_ref = date_el.get("reference") if date_el is not None else None
        if date_ref:
            # XStream may serialise a shared LocalDateTime object by reference
            # (e.g. portfolio-transaction sharing its paired account-transaction's date).
            ref_target = _resolve_reference(root, [*tx_path, "date"], date_ref)
            date_str = _text(ref_target) if ref_target is not None else None
        else:
            date_str = _text(date_el)

        if not date_str:
            warnings.append(f"Skipped transaction {uuid or '?'} — missing date.")
            return None

        try:
            date = datetime.fromisoformat(date_str)
        except ValueError:
            warnings.append(
                f"Skipped transaction {uuid or '?'} — invalid date {date_str!r}."
            )
            return None

        amount = _decode_amount(_text(tx_el.find("amount")))
        shares = _decode_shares(_text(tx_el.find("shares")))
        currency = _text(tx_el.find("currencyCode")) or ""
        note = _text(tx_el.find("note"))

        security = self._resolve_security(
            tx_el.find("security"), root, tx_path, securities, warnings
        )

        units = self._parse_units(tx_el.find("units"))

        return ParsedTransaction(
            kind=kind,
            uuid=uuid,
            date=date,
            type=tx_type,
            amount=amount,
            currency=currency,
            shares=shares,
            note=note,
            security=security,
            units=units,
        )

    def _parse_units(self, units_el: ET.Element | None) -> list[Unit]:
        if units_el is None:
            return []
        out: list[Unit] = []
        for unit in units_el.findall("unit"):
            utype = unit.get("type") or ""
            amount_el = unit.find("amount")
            if amount_el is None:
                continue
            raw = amount_el.get("amount") or _text(amount_el) or "0"
            currency = amount_el.get("currency") or ""
            out.append(
                Unit(type=utype, amount=_decode_amount(raw), currency=currency)
            )
        return out

    def _resolve_security(
        self,
        sec_el: ET.Element | None,
        root: ET.Element,
        tx_path: list[str],
        securities: dict[str, SecurityInfo],
        warnings: list[str],
    ) -> SecurityInfo | None:
        if sec_el is None:
            return None

        # Direct UUID child — rare, but supported.
        direct_uuid = _text(sec_el.find("uuid"))
        if direct_uuid:
            return securities.get(direct_uuid) or SecurityInfo(uuid=direct_uuid)

        reference = sec_el.get("reference")
        if not reference:
            return None

        # The reference is relative to the <security> element, which is a
        # direct child of the transaction element.
        target = _resolve_reference(root, [*tx_path, "security"], reference)
        if target is None:
            warnings.append(f"Could not resolve security reference {reference!r}.")
            return None
        uuid = _text(target.find("uuid"))
        if not uuid:
            return None
        return securities.get(uuid) or SecurityInfo(uuid=uuid)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _text(element: ET.Element | None) -> str | None:
    if element is None:
        return None
    return (element.text or "").strip() or None


def _decode_amount(raw: str | None) -> Decimal:
    """Portfolio Performance stores monetary amounts as long integers in cents (÷100)."""
    if not raw:
        return Decimal("0")
    try:
        return (Decimal(raw) / _AMOUNT_FACTOR).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0")


def _decode_shares(raw: str | None) -> Decimal:
    """Portfolio Performance stores share quantities as long integers with 8 decimal places (÷100_000_000)."""
    if not raw:
        return Decimal("0")
    try:
        return (Decimal(raw) / _SHARE_FACTOR).quantize(Decimal("0.00000001"))
    except Exception:
        return Decimal("0")



def _resolve_reference(
    root: ET.Element, tx_path: list[str], reference: str
) -> ET.Element | None:
    """Resolve an XStream ``XPATH_RELATIVE_REFERENCES`` string.

    ``reference`` is of the form ``../../../securities/security`` or
    ``../../../securities/security[2]``.  We walk ``tx_path`` backwards for
    every ``..`` segment, then descend into the remaining segments from the
    resulting element.
    """
    parts = [p for p in reference.split("/") if p]
    up = 0
    while up < len(parts) and parts[up] == "..":
        up += 1

    remaining_path = tx_path[: len(tx_path) - up] if up else list(tx_path)
    remaining_path.extend(parts[up:])

    # The first segment is always "client" which is the root element itself.
    if not remaining_path or remaining_path[0] != root.tag:
        return None

    current: ET.Element | None = root
    for segment in remaining_path[1:]:
        if current is None:
            return None
        name, index = _split_index(segment)
        matches = current.findall(name)
        if not matches:
            return None
        if index is None:
            current = matches[0]
        else:
            if index < 1 or index > len(matches):
                return None
            current = matches[index - 1]
    return current


def _split_index(segment: str) -> tuple[str, int | None]:
    if "[" in segment and segment.endswith("]"):
        name, rest = segment.split("[", 1)
        try:
            return name, int(rest[:-1])
        except ValueError:
            return segment, None
    return segment, None
