"""Shared order-reference helpers for broker PDF/XML cross-source deduplication.

Both ING and comdirect settlement PDFs carry a stable *Ordernummer* that
Portfolio Performance copies verbatim into the XML transaction note.  Deriving
the same ``external_uuid`` from this reference on both sides lets the
``uq_transaction_external_uuid`` constraint dedupe a trade across sources,
regardless of which file is imported first.  This module is the single source
of truth for both the parsing and the key format, so the two importers can
never drift apart.

ING format (PDF text and PP XML note):
    ``Ordernummer 456480204.001``

Comdirect formats:
    PDF:     ``Ordernummer : 000512215771-001``
    PP note: ``Ord.-Nr.: 072324316214-001 | R.-Nr.: <rechnungsnr>``
"""

from __future__ import annotations

import re

# "Ordernummer 456480204.001" — used in ING PDF text and PP XML notes alike.
_ING_ORDER_RE = re.compile(r"Ordernummer\s+([0-9][0-9.\-]*)")

# Modern comdirect PP note prefix, e.g. "Ord.-Nr.: 072324316214-001 | R.-Nr.: 1234567".
# The reference is digits and dashes and stops before the " | R.-Nr.:" segment.
# The legacy " / "-separated "Order-Nr.: 71871368321 / 001" form has no
# cross-source PDF counterpart and deliberately does not match (returns None):
# "Order-Nr." has no dot after "Ord", so the anchored pattern never fires.
_ORDER_REF_RE = re.compile(r"^Ord\.-Nr\.:\s*([0-9][0-9-]*)")


def parse_ing_order_ref(note: str | None) -> str | None:
    """Extract the ING Ordernummer from a PDF text line or PP XML note.

    Matches both ``"Ordernummer 456480204.001"`` (PDF) and the same string
    when PP copies it verbatim into the XML ``<note>`` element.  Returns the
    reference (e.g. ``"456480204.001"``) or ``None`` when *note* is absent or
    does not match.
    """
    if not note:
        return None
    m = _ING_ORDER_RE.search(note)
    return m.group(1) if m else None


def build_ing_external_uuid(ref: str) -> str:
    """Return the shared ``external_uuid`` key for an ING order *ref*.

    Single source of truth for the ING cross-source key; output is
    byte-identical to ``build_pdf_external_uuid("ing", ref)``.
    """
    return build_pdf_external_uuid("ing", ref)


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
