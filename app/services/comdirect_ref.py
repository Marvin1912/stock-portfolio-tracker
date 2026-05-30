"""Shared comdirect order-reference helpers.

The comdirect *Ordernummer* is the only stable identifier shared between the
two import paths for the same trade:

* the comdirect settlement **PDF** (parsed by :mod:`app.services.comdirect_parser`),
  which reads ``Ordernummer : 000512215771-001``; and
* the Portfolio Performance **XML** export, whose ``ComdirectPDFExtractor``
  writes the same number into the transaction *note* as
  ``Ord.-Nr.: 072324316214-001 | R.-Nr.: <rechnungsnr>``.

Deriving the same ``external_uuid`` from this reference on both sides lets the
``uq_transaction_external_uuid`` constraint dedupe a trade across sources,
regardless of which file is imported first.  This module is the single source
of truth for both the parsing and the key format, so the two importers can
never drift apart.
"""

from __future__ import annotations

import datetime
import re
from decimal import ROUND_HALF_UP, Decimal

# Modern PP note prefix, e.g. "Ord.-Nr.: 072324316214-001 | R.-Nr.: 1234567".
# The reference is digits and dashes and stops before the " | R.-Nr.:" segment.
# The legacy " / "-separated "Order-Nr.: 71871368321 / 001" form has no
# cross-source PDF counterpart and deliberately does not match (returns None):
# "Order-Nr." has no dot after "Ord", so the anchored pattern never fires.
_ORDER_REF_RE = re.compile(r"^Ord\.-Nr\.:\s*([0-9][0-9-]*)")


def parse_comdirect_order_ref(note: str | None) -> str | None:
    """Extract the comdirect Ordernummer from a Portfolio Performance *note*.

    Returns the normalised reference (e.g. ``"072324316214-001"``) or ``None``
    when *note* is empty, not a comdirect note, or uses the legacy ``Order-Nr.``
    ``/``-separated form (which the PDF importer does not parse, so it has no
    cross-source counterpart to dedupe against).
    """
    if not note:
        return None
    m = _ORDER_REF_RE.match(note.strip())
    if m is None:
        return None
    return m.group(1)


def build_pdf_external_uuid(broker: str, ref: str) -> str:
    """Return the ``external_uuid`` key for a PDF-imported *broker* trade *ref*.

    The ``pdf:{broker}:{ref}`` shape namespaces dedupe keys per broker so that
    e.g. an ING order number can never collide with a comdirect one.
    """
    return f"pdf:{broker}:{ref}"


def build_comdirect_external_uuid(ref: str) -> str:
    """Return the shared ``external_uuid`` key for a comdirect order *ref*.

    Kept as the single source of truth for the comdirect key, which is shared
    with the Portfolio Performance XML importer for cross-source dedupe; the
    output is byte-identical to ``build_pdf_external_uuid("comdirect", ref)``.
    """
    return build_pdf_external_uuid("comdirect", ref)


# Portfolio Performance auto-text for a savings-plan-generated trade, e.g.
# "Generiert von Sparplan 'iShares EM' am 16.02.2025, 11:12". These carry no
# order number (unlike comdirect notes), so the order-number bridge cannot link
# them to the matching ING PDF — the natural key below is used instead.
_SPARPLAN_NOTE_RE = re.compile(r"Generiert von Sparplan")


def is_sparplan_note(note: str | None) -> bool:
    """Return True if *note* is Portfolio Performance's savings-plan auto-text."""
    return bool(note and _SPARPLAN_NOTE_RE.search(note))


def build_natural_trade_uuid(
    isin: str,
    trade_date: datetime.datetime,
    total: Decimal,
    ttype: str,
) -> str:
    """Return a deterministic cross-source key from a trade's natural identity.

    Used when no order number bridges the two sources (e.g. an ING Sparplan: the
    PDF has an ``Ordernummer`` but the Portfolio Performance transaction, being
    PP-generated, does not). Both importers can compute this same key from values
    that are identical for the same real trade — ISIN, the calendar trade date,
    the fee-inclusive total (PP's ``amount`` == the ING ``Endbetrag`` ==
    ``Kurswert + Provision``) and the trade type — so the
    ``uq_transaction_external_uuid`` constraint dedupes them regardless of import
    order. ``total`` is rendered as integer cents so formatting can never differ.
    """
    cents = int((total * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return f"nat:{isin.upper()}:{trade_date.date().isoformat()}:{cents}:{ttype.upper()}"
