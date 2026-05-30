"""Tests for the shared broker order-reference helpers."""

from __future__ import annotations

import pytest

from app.services.comdirect_ref import (
    build_comdirect_external_uuid,
    build_ing_external_uuid,
    parse_comdirect_order_ref,
    parse_ing_order_ref,
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
        # ING PDF text / PP XML note — both use the same format.
        ("Ordernummer 456480204.001", "456480204.001"),
        ("Ordernummer 463890395.001", "463890395.001"),
        # Leading/trailing whitespace is tolerated.
        ("  Ordernummer 123456789.001  ", "123456789.001"),
        # Not an ING note.
        (None, None),
        ("", None),
        ("Dividend Payment", None),
        # Comdirect PP note must not match.
        ("Ord.-Nr.: 072324316214-001 | R.-Nr.: 9988776655", None),
    ],
)
def test_parse_ing_order_ref(note: str | None, expected: str | None) -> None:
    assert parse_ing_order_ref(note) == expected


def test_build_ing_external_uuid() -> None:
    assert build_ing_external_uuid("456480204.001") == "pdf:ing:456480204.001"


def test_ing_roundtrip_note_to_key() -> None:
    """An ING XML note maps to the same key the PDF importer builds."""
    note = "Ordernummer 463890395.001"
    ref = parse_ing_order_ref(note)
    assert ref is not None
    assert build_ing_external_uuid(ref) == "pdf:ing:463890395.001"
