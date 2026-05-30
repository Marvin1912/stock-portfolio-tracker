"""Tests for the shared comdirect order-reference helpers."""

from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from app.services.comdirect_ref import (
    build_comdirect_external_uuid,
    build_natural_trade_uuid,
    is_sparplan_note,
    parse_comdirect_order_ref,
)


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        # Modern PP note → reference, stopping before " | R.-Nr.:".
        ("Ord.-Nr.: 072324316214-001 | R.-Nr.: 9988776655", "072324316214-001"),
        # Leading/trailing whitespace is trimmed.
        ("  Ord.-Nr.: 000512215771-001 | R.-Nr.: 1 ", "000512215771-001"),
        # Note without the rechnungsnr segment.
        ("Ord.-Nr.: 072324316214-001", "072324316214-001"),
        # No note at all.
        (None, None),
        ("", None),
        # Non-comdirect notes.
        ("Dividend Payment", None),
        ("Purchase via Sparplan", None),
        # Legacy " / "-separated form has no PDF counterpart → fall back.
        ("Order-Nr.: 71871368321 / 001", None),
    ],
)
def test_parse_comdirect_order_ref(note: str | None, expected: str | None) -> None:
    assert parse_comdirect_order_ref(note) == expected


def test_build_comdirect_external_uuid() -> None:
    assert (
        build_comdirect_external_uuid("072324316214-001")
        == "pdf:comdirect:072324316214-001"
    )


def test_roundtrip_note_to_key() -> None:
    """A PP note maps to the same key the PDF importer builds from the raw ref."""
    note = "Ord.-Nr.: 000512215771-001 | R.-Nr.: 42"
    ref = parse_comdirect_order_ref(note)
    assert ref is not None
    assert build_comdirect_external_uuid(ref) == "pdf:comdirect:000512215771-001"


@pytest.mark.parametrize(
    ("note", "expected"),
    [
        ("Generiert von Sparplan 'iShares EM' am 16.02.2025, 11:12", True),
        ("Generiert von Sparplan 'Core World'", True),
        # comdirect / unrelated / empty notes are not savings-plan notes.
        ("Ord.-Nr.: 072324316214-001 | R.-Nr.: 9988776655", False),
        ("Purchase via Sparplan", False),
        ("", False),
        (None, False),
    ],
)
def test_is_sparplan_note(note: str | None, expected: bool) -> None:
    assert is_sparplan_note(note) is expected


def test_build_natural_trade_uuid() -> None:
    key = build_natural_trade_uuid(
        "IE00B4L5Y983",
        datetime.datetime(2025, 1, 2, tzinfo=datetime.UTC),
        Decimal("25.00"),
        "BUY",
    )
    assert key == "nat:IE00B4L5Y983:2025-01-02:2500:BUY"


def test_build_natural_trade_uuid_rounds_to_cents() -> None:
    # 967.64 + 7.32 = 974.96 → 97496 cents; lowercase isin/type are normalised.
    key = build_natural_trade_uuid(
        "ie00b4l5y983",
        datetime.datetime(2026, 3, 23),
        Decimal("974.96"),
        "buy",
    )
    assert key == "nat:IE00B4L5Y983:2026-03-23:97496:BUY"
