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

import re

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


def build_comdirect_external_uuid(ref: str) -> str:
    """Return the shared ``external_uuid`` key for a comdirect order *ref*."""
    return f"pdf:comdirect:{ref}"
