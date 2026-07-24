"""Tests for IMAP fetch_messages operation."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import (
    ImapClient,
    ImapError,
)
from tests.conftest import _make_mock_imap_ssl


# ---------------------------------------------------------------------------
# fetch_messages
# ---------------------------------------------------------------------------


def test_fetch_messages_returns_uid_body_pairs(cfg: MailConfig) -> None:
    """fetch_messages returns (uid, raw_bytes) for each fetched message."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = (
        "OK",
        [
            (b"1 (UID 1)", b"msg1-body"),
            (b"2 (UID 2)", b"msg2-body"),
        ],
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.fetch_messages([1, 2])

    assert result == [(1, b"msg1-body"), (2, b"msg2-body")]


def test_fetch_messages_uses_body_peek(cfg: MailConfig) -> None:
    r"""fetch_messages uses BODY.PEEK[] so the \Seen flag is NOT set."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("OK", [])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.fetch_messages([1])

    mock_ssl.uid.assert_called_once_with("FETCH", "1", "(BODY.PEEK[])")


def test_fetch_messages_multiple_uids_comma_separated(cfg: MailConfig) -> None:
    """fetch_messages builds a comma-separated UID set."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("OK", [])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.fetch_messages([10, 20, 30])

    mock_ssl.uid.assert_called_once_with("FETCH", "10,20,30", "(BODY.PEEK[])")


def test_fetch_messages_skips_missing_uids(cfg: MailConfig) -> None:
    """fetch_messages silently omits UIDs that the server didn't return."""
    mock_ssl = _make_mock_imap_ssl()
    # Server only returns UID 1, not 2 (UID 2 was deleted between
    # SEARCH and FETCH).
    mock_ssl.uid.return_value = (
        "OK",
        [(b"1 (UID 1)", b"body1")],
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.fetch_messages([1, 2])

    assert result == [(1, b"body1")]


def test_fetch_messages_empty_uids(cfg: MailConfig) -> None:
    """fetch_messages returns [] when given an empty UID list."""
    mock_ssl = _make_mock_imap_ssl()

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.fetch_messages([])

    assert result == []
    mock_ssl.uid.assert_not_called()


def test_fetch_messages_not_connected(cfg: MailConfig) -> None:
    """fetch_messages raises ImapError when not connected."""
    client = ImapClient(cfg)
    with pytest.raises(ImapError, match="Not connected"):
        client.fetch_messages([1])


def test_fetch_messages_server_error(cfg: MailConfig) -> None:
    """fetch_messages raises ImapError on non-OK response."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("NO", [b"Some error"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapError, match="UID FETCH failed"):
                client.fetch_messages([1])


def test_fetch_messages_skips_non_tuple_items(cfg: MailConfig) -> None:
    """fetch_messages ignores non-tuple items in the response."""
    mock_ssl = _make_mock_imap_ssl()
    # imaplib sometimes returns a trailing closing ")" as a bytes item.
    mock_ssl.uid.return_value = (
        "OK",
        [
            b"1 (UID 1 BODY[] {5}",
            b"body1",
            b")",
            b"2 (UID 2 BODY[] {5}",
            b"body2",
            b")",
            b")",  # trailing ")" from imaplib — should be skipped
        ],
    )

    def fake_uid(
        cmd: str, uid_set: str, fetch_spec: str
    ) -> tuple[str, list[tuple[bytes, bytes]]]:
        # Return a properly structured response that imaplib will process
        # into (header, body) tuples.
        return (
            "OK",
            [
                (b"1 (UID 1)", b"body1"),
                (b"2 (UID 2)", b"body2"),
            ],
        )

    mock_ssl.uid.side_effect = fake_uid

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.fetch_messages([1, 2])

    assert result == [(1, b"body1"), (2, b"body2")]


def test_fetch_messages_trailing_uid_exchange_shape(cfg: MailConfig) -> None:
    """Exchange/Office365 returns the UID as a trailing bare-bytes item."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = (
        "OK",
        [
            (b"1 (BODY[] {9}", b"msg1-body"),
            b" UID 10780)",
            (b"2 (BODY[] {9}", b"msg2-body"),
            b" UID 10781)",
        ],
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.fetch_messages([10780, 10781])

    assert result == [(10780, b"msg1-body"), (10781, b"msg2-body")]


def test_fetch_messages_standalone_bare_bytes_ignored(cfg: MailConfig) -> None:
    """A bare-bytes item with no preceding header-less tuple is ignored."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = (
        "OK",
        [
            (b"1 (UID 1 BODY[] {5}", b"body1"),
            b")",  # standalone continuation — not a UID carrier
        ],
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.fetch_messages([1])

    assert result == [(1, b"body1")]


def test_fetch_messages_header_with_body_size(cfg: MailConfig) -> None:
    """fetch_messages parses UID from headers containing BODY[] size."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = (
        "OK",
        [
            (b"1 (UID 42 BODY[] {5}", b"abcde"),
        ],
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.fetch_messages([42])

    assert result == [(42, b"abcde")]
