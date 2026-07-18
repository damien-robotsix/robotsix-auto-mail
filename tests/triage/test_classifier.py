"""Tests for shared JSON watermark helpers."""

from __future__ import annotations

from unittest import mock

from robotsix_auto_mail.db import load_json_watermark


def test_load_json_watermark_returns_dict_for_valid_json() -> None:
    conn = mock.MagicMock()
    with mock.patch(
        "robotsix_auto_mail.db.queries.get_watermark",
        return_value='{"a": 1}',
    ):
        result = load_json_watermark(conn, "test-key")
    assert result == {"a": 1}


def test_load_json_watermark_returns_empty_for_none() -> None:
    conn = mock.MagicMock()
    with mock.patch(
        "robotsix_auto_mail.db.queries.get_watermark",
        return_value=None,
    ):
        result = load_json_watermark(conn, "test-key")
    assert result == {}


def test_load_json_watermark_returns_empty_for_json_array() -> None:
    """A JSON array should not pass the isinstance(dict) guard."""
    conn = mock.MagicMock()
    with mock.patch(
        "robotsix_auto_mail.db.queries.get_watermark",
        return_value="[1, 2, 3]",
    ):
        result = load_json_watermark(conn, "test-key")
    assert result == {}


def test_load_json_watermark_returns_empty_for_corrupt_json() -> None:
    conn = mock.MagicMock()
    with mock.patch(
        "robotsix_auto_mail.db.queries.get_watermark",
        return_value="not-valid-json{",
    ):
        result = load_json_watermark(conn, "test-key")
    assert result == {}
