"""Tests for IMAP fetch_envelopes operation."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import (
    ImapClient,
    ImapError,
)
from tests.conftest import _make_mock_imap_ssl


def _envelope_fetch_response(
    *entries: tuple[int, str, str, str],
) -> tuple[str, list[bytes]]:
    """Build a mock ``UID FETCH (ENVELOPE)`` response.

    Each *entry* is ``(uid, subject, from_addr, date)``.
    Returns the ``(status, data)`` tuple that ``imaplib.IMAP4.uid`` returns.

    For inline responses (no literals), imaplib returns bare ``bytes`` items.
    """
    items: list[bytes] = []
    for uid, subject, from_addr, date_str in entries:
        # Parse the from_addr into personal + mailbox + host parts.
        if "<" in from_addr and ">" in from_addr:
            personal = from_addr[: from_addr.index("<")].strip()
            addr = from_addr[from_addr.index("<") + 1 : from_addr.index(">")]
            if "@" in addr:
                mailbox, host = addr.split("@", 1)
            else:
                mailbox, host = addr, "example.com"
        else:
            personal = from_addr
            mailbox = "user"
            host = "example.com"

        if personal:
            addr_part = f'("{personal}" NIL "{mailbox}" "{host}")'
        else:
            addr_part = f'(NIL NIL "{mailbox}" "{host}")'

        msg_id = f"<uid{uid}@example.com>"

        # Build the inline FETCH response line.
        line = (
            f'1 (UID {uid} FLAGS (\\Seen) INTERNALDATE "{date_str}" '
            f"RFC822.SIZE 1234 ENVELOPE "
            f'("{date_str}" "{subject}" ({addr_part}) '
            f'NIL NIL NIL NIL NIL NIL "{msg_id}"))'
        )
        items.append(line.encode())
    return ("OK", items)


def test_fetch_envelopes_returns_metadata(cfg: MailConfig) -> None:
    """fetch_envelopes returns subject, from, date, size, flags for each message."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = _envelope_fetch_response(
        (1, "Hello", "Alice <user@example.com>", "01-Jan-2024 12:00:00 +0000"),
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.fetch_envelopes([1])

    assert len(result) == 1
    msg = result[0]
    assert msg["uid"] == 1
    assert msg["subject"] == "Hello"
    assert "Alice" in str(msg["from"])
    assert "2024" in str(msg["date"])
    assert msg["size"] == 1234
    assert "\\Seen" in msg["flags"]
    assert msg["message_id"] == "<uid1@example.com>"


def test_fetch_envelopes_multiple_messages(cfg: MailConfig) -> None:
    """fetch_envelopes returns one dict per message."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = _envelope_fetch_response(
        (1, "Subject 1", "A <a@x.com>", "01-Jan-2024 12:00:00 +0000"),
        (2, "Subject 2", "B <b@x.com>", "02-Jan-2024 12:00:00 +0000"),
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.fetch_envelopes([1, 2])

    assert len(result) == 2
    assert result[0]["uid"] == 1
    assert result[1]["uid"] == 2


def test_fetch_envelopes_empty_uids(cfg: MailConfig) -> None:
    """fetch_envelopes returns [] for an empty UID list."""
    mock_ssl = _make_mock_imap_ssl()

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.fetch_envelopes([])

    assert result == []
    mock_ssl.uid.assert_not_called()


def test_fetch_envelopes_not_connected(cfg: MailConfig) -> None:
    """fetch_envelopes raises ImapError when not connected."""
    client = ImapClient(cfg)
    with pytest.raises(ImapError, match="Not connected"):
        client.fetch_envelopes([1])


def test_fetch_envelopes_server_error(cfg: MailConfig) -> None:
    """fetch_envelopes raises ImapError on non-OK response."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("NO", [b"Some error"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapError, match="UID FETCH"):
                client.fetch_envelopes([1])
