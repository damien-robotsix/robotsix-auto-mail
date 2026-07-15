"""Tests for the shared date-formatting helper."""

from __future__ import annotations

from robotsix_auto_mail.core.format import _format_date


def test_format_date_with_time() -> None:
    """A valid ISO-8601 datetime is formatted with seconds dropped."""
    assert _format_date("2026-06-05T15:13:16") == "2026-06-05 15:13"


def test_format_date_date_only() -> None:
    """A date-only ISO-8601 string defaults to midnight."""
    assert _format_date("2026-06-05") == "2026-06-05 00:00"


def test_format_date_with_timezone_offset() -> None:
    """A timezone offset is rendered as local wall-clock fields, not converted."""
    assert _format_date("2026-06-05T15:13:16+02:00") == "2026-06-05 15:13"


def test_format_date_malformed() -> None:
    """A malformed string is returned unchanged via the fallback branch."""
    assert _format_date("not-a-date") == "not-a-date"


def test_format_date_empty() -> None:
    """An empty string is returned unchanged."""
    assert _format_date("") == ""


def test_format_date_none() -> None:
    """A ``None`` input hits the ``TypeError`` branch and is returned unchanged."""
    assert _format_date(None) is None  # type: ignore[arg-type]


def test_format_date_non_string() -> None:
    """A non-string input hits the ``TypeError`` branch and is returned unchanged."""
    sentinel: object = 12345
    assert _format_date(sentinel) == sentinel  # type: ignore[arg-type]
