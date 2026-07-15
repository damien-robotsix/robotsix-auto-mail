"""Tests for the _format_date helper."""

from __future__ import annotations

from robotsix_auto_mail.core.format import _format_date

# ---------------------------------------------------------------------------
# _format_date
# ---------------------------------------------------------------------------


def test_format_date_valid_iso() -> None:
    assert _format_date("2025-03-15T09:30:00") == "2025-03-15 09:30"


def test_format_date_with_tz_offset() -> None:
    result = _format_date("2025-06-01T14:00:00+00:00")
    assert result.startswith("2025-06-01")


def test_format_date_invalid_returns_raw() -> None:
    assert _format_date("Last Thursday") == "Last Thursday"


def test_format_date_none_returns_none() -> None:
    assert _format_date(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers for tests that need a file-based DB
# ---------------------------------------------------------------------------
