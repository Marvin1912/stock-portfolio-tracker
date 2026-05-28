"""Tests for the in-memory chart result cache."""

from __future__ import annotations

from unittest.mock import patch

from app.services import chart_cache


def setup_function() -> None:
    chart_cache.invalidate()


def test_miss_returns_none() -> None:
    assert chart_cache.get("nonexistent") is None


def test_set_and_hit() -> None:
    chart_cache.set("k", [1, 2, 3])
    assert chart_cache.get("k") == [1, 2, 3]


def test_ttl_expiry_returns_none() -> None:
    with patch("app.services.chart_cache.time") as mock_time:
        mock_time.monotonic.return_value = 0.0
        chart_cache.set("k", "data")

        # Still within TTL
        mock_time.monotonic.return_value = 299.0
        assert chart_cache.get("k") == "data"

        # Exactly at TTL boundary — expired
        mock_time.monotonic.return_value = 300.0
        assert chart_cache.get("k") is None


def test_invalidate_clears_all() -> None:
    chart_cache.set("a", 1)
    chart_cache.set("b", 2)
    chart_cache.invalidate()
    assert chart_cache.get("a") is None
    assert chart_cache.get("b") is None


def test_overwrite_updates_timestamp() -> None:
    with patch("app.services.chart_cache.time") as mock_time:
        mock_time.monotonic.return_value = 0.0
        chart_cache.set("k", "old")

        mock_time.monotonic.return_value = 290.0
        chart_cache.set("k", "new")

        # 290 + 290 = 580 > 300 from original write, but < 300 from re-write
        mock_time.monotonic.return_value = 580.0
        assert chart_cache.get("k") == "new"

        mock_time.monotonic.return_value = 591.0
        assert chart_cache.get("k") is None
