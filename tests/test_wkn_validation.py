"""Tests for WKN validation in schemas and the HTMX validate-wkn endpoint."""

from __future__ import annotations

import pytest
from pydantic import ValidationError  # noqa: I001

from app.schemas.holdings import HoldingCreate

# ---------------------------------------------------------------------------
# Schema-level WKN validation
# ---------------------------------------------------------------------------


def test_wkn_valid_alphanumeric() -> None:
    h = HoldingCreate(wkn="865985", quantity="10")
    assert h.wkn == "865985"


def test_wkn_valid_mixed_case_normalised_to_upper() -> None:
    h = HoldingCreate(wkn="abc123", quantity="5")
    assert h.wkn == "ABC123"


def test_wkn_valid_all_letters() -> None:
    h = HoldingCreate(wkn="AAPLXY", quantity="1")
    assert h.wkn == "AAPLXY"


def test_wkn_valid_all_digits() -> None:
    h = HoldingCreate(wkn="123456", quantity="1")
    assert h.wkn == "123456"


def test_wkn_too_short_raises() -> None:
    with pytest.raises(ValidationError):
        HoldingCreate(wkn="AAPL", quantity="10")


def test_wkn_too_long_raises() -> None:
    with pytest.raises(ValidationError):
        HoldingCreate(wkn="AAPL123", quantity="10")


def test_wkn_with_special_chars_raises() -> None:
    with pytest.raises(ValidationError):
        HoldingCreate(wkn="AAP-12", quantity="10")


def test_wkn_with_space_raises() -> None:
    with pytest.raises(ValidationError):
        HoldingCreate(wkn="AAP 12", quantity="10")


def test_wkn_empty_raises() -> None:
    with pytest.raises(ValidationError):
        HoldingCreate(wkn="", quantity="10")
