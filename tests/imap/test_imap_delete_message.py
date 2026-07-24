"""Tests for IMAP delete_message (single) operation."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import (
    ImapClient,
    ImapError,
    ImapMessageNotFoundError,
)
from tests.conftest import _make_mock_imap_ssl
from tests.imap.conftest import _uid_side_effect


# ---------------------------------------------------------------------------
# delete_message
# ---------------------------------------------------------------------------


def test_delete_message_success(cfg: MailConfig) -> None:
    """delete_message marks a message \\Deleted and expunges it."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.side_effect = _uid_side_effect(
        search_result=("OK", [b"10"]),
        other=("OK", [b""]),
    )
    mock_ssl.expunge.return_value = ("OK", [b""])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.delete_message(10)

    mock_ssl.uid.assert_any_call("STORE", "10", "+FLAGS", "(\\Deleted)")
    mock_ssl.expunge.assert_called_once()


def test_delete_message_uid_not_found_raises(cfg: MailConfig) -> None:
    """delete_message raises ImapMessageNotFoundError for absent UIDs."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("OK", [b""])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapMessageNotFoundError, match="10"):
                client.delete_message(10)

    mock_ssl.expunge.assert_not_called()


def test_delete_message_not_connected(cfg: MailConfig) -> None:
    """delete_message raises ImapError when the client is not connected."""
    client = ImapClient(cfg)
    with pytest.raises(ImapError, match="Not connected"):
        client.delete_message(1)


def test_delete_message_store_fails(cfg: MailConfig) -> None:
    """delete_message raises ImapError when UID STORE returns non-OK."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.side_effect = _uid_side_effect(
        search_result=("OK", [b"10"]),
        other=("NO", [b"PERMISSION_DENIED"]),
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapError, match="UID STORE"):
                client.delete_message(10)

    mock_ssl.expunge.assert_not_called()


def test_delete_message_expunge_fails(cfg: MailConfig) -> None:
    """delete_message raises ImapError when EXPUNGE fails."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.side_effect = _uid_side_effect(
        search_result=("OK", [b"1"]),
        other=("OK", [b""]),
    )
    mock_ssl.expunge.return_value = ("NO", [b""])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapError, match="EXPUNGE"):
                client.delete_message(1)
