"""Tests for IMAP search_uids operation."""

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
# search_uids
# ---------------------------------------------------------------------------


def test_search_uids_returns_uids(cfg: MailConfig) -> None:
    """search_uids parses space-separated UIDs from the SEARCH response."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("OK", [b"1 2 3"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.search_uids()

    mock_ssl.uid.assert_called_once_with("SEARCH", "ALL")
    assert result == [1, 2, 3]


def test_search_uids_empty_result(cfg: MailConfig) -> None:
    """search_uids returns [] when SEARCH finds nothing."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("OK", [b""])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.search_uids()

    assert result == []


def test_search_uids_empty_data_list(cfg: MailConfig) -> None:
    """search_uids returns [] when data list is empty."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("OK", [])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.search_uids()

    assert result == []


def test_search_uids_custom_criteria(cfg: MailConfig) -> None:
    """search_uids passes custom criteria through."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("OK", [b"42 43"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.search_uids("UID 41:*")

    mock_ssl.uid.assert_called_once_with("SEARCH", "UID 41:*")
    assert result == [42, 43]


def test_search_uids_not_connected(cfg: MailConfig) -> None:
    """search_uids raises ImapError when the client is not connected."""
    client = ImapClient(cfg)
    with pytest.raises(ImapError, match="Not connected"):
        client.search_uids()


def test_search_uids_server_error(cfg: MailConfig) -> None:
    """search_uids raises ImapError on non-OK response."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("NO", [b"Server error"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapError, match="UID SEARCH failed"):
                client.search_uids()


def test_search_uids_single_uid(cfg: MailConfig) -> None:
    """search_uids works when only one UID matches."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.return_value = ("OK", [b"99"])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            result = client.search_uids()

    assert result == [99]
