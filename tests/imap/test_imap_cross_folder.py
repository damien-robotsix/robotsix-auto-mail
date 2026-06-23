"""Tests for cross_folder_resolve and COPYUID parsing."""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.imap import (
    ImapClient,
    ImapError,
    MailboxInfo,
    cross_folder_resolve,
)

# ---------------------------------------------------------------------------
# cross_folder_resolve
# ---------------------------------------------------------------------------


def test_cross_folder_resolve_found_in_another_folder() -> None:
    """cross_folder_resolve finds a message relocated to a different folder."""
    client = mock.MagicMock(spec=ImapClient)
    client.list_folders.return_value = [
        MailboxInfo(name="INBOX", attributes=(), delimiter="/"),
        MailboxInfo(name="Archive", attributes=(), delimiter="/"),
        MailboxInfo(name="Sent", attributes=(), delimiter="/"),
    ]
    client.select_folder.return_value = 5
    # INBOX: no match; Archive: match UID 7
    client.search_uids.side_effect = [
        [],  # INBOX
        [7],  # Archive
    ]

    result = cross_folder_resolve(client, "<test@example.com>")

    assert result == ("Archive", 7)
    assert client.select_folder.call_args_list == [
        mock.call("INBOX"),
        mock.call("Archive"),
    ]


def test_cross_folder_resolve_not_found_anywhere() -> None:
    """cross_folder_resolve returns None when absent from all non-waste folders."""
    client = mock.MagicMock(spec=ImapClient)
    client.list_folders.return_value = [
        MailboxInfo(name="INBOX", attributes=(), delimiter="/"),
        MailboxInfo(name="Sent", attributes=(), delimiter="/"),
    ]
    client.select_folder.return_value = 5
    client.search_uids.return_value = []  # never found

    result = cross_folder_resolve(client, "<gone@example.com>")

    assert result is None


def test_cross_folder_resolve_skips_waste_folders() -> None:
    """cross_folder_resolve ignores Trash/Junk folders — returns None."""
    client = mock.MagicMock(spec=ImapClient)
    client.list_folders.return_value = [
        MailboxInfo(name="INBOX", attributes=(), delimiter="/"),
        MailboxInfo(name="Trash", attributes=(), delimiter="/"),
    ]
    # The message is only in Trash (waste), nowhere else.
    client.select_folder.return_value = 5
    client.search_uids.side_effect = [
        [],  # INBOX — no match
    ]
    # Trash is skipped entirely, so search_uids is only called once (for INBOX).

    result = cross_folder_resolve(client, "<trashed@example.com>")

    assert result is None
    assert client.select_folder.call_count == 1
    client.select_folder.assert_called_once_with("INBOX")


def test_cross_folder_resolve_skips_noselect_folders() -> None:
    """A \\Noselect container (e.g. Gmail's ``[Gmail]``) is never SELECT-ed."""
    client = mock.MagicMock(spec=ImapClient)
    client.list_folders.return_value = [
        MailboxInfo(name="INBOX", attributes=("\\HasNoChildren",), delimiter="/"),
        MailboxInfo(
            name="[Gmail]", attributes=("\\HasChildren", "\\Noselect"), delimiter="/"
        ),
        MailboxInfo(name="[Gmail]/All Mail", attributes=("\\All",), delimiter="/"),
    ]
    client.select_folder.return_value = 5
    client.search_uids.side_effect = [
        [],  # INBOX — no match
        [9],  # [Gmail]/All Mail — match
    ]

    result = cross_folder_resolve(client, "<m@example.com>")

    assert result == ("[Gmail]/All Mail", 9)
    # [Gmail] (Noselect) is skipped — never SELECT-ed.
    assert client.select_folder.call_args_list == [
        mock.call("INBOX"),
        mock.call("[Gmail]/All Mail"),
    ]


def test_cross_folder_resolve_returns_first_match() -> None:
    """cross_folder_resolve returns the first found match (short-circuits)."""
    client = mock.MagicMock(spec=ImapClient)
    client.list_folders.return_value = [
        MailboxInfo(name="INBOX", attributes=(), delimiter="/"),
        MailboxInfo(name="Projects", attributes=(), delimiter="/"),
        MailboxInfo(name="Archive", attributes=(), delimiter="/"),
    ]
    client.select_folder.return_value = 5
    # Found in Projects (UID 3); Archive should not be searched.
    client.search_uids.side_effect = [
        [],  # INBOX
        [3],  # Projects
    ]

    result = cross_folder_resolve(client, "<msg@example.com>")

    assert result == ("Projects", 3)
    # Only two folders searched — Archive is never touched.
    assert client.select_folder.call_count == 2


def test_cross_folder_resolve_propagates_imap_error() -> None:
    """cross_folder_resolve does not swallow ImapError from IMAP operations."""
    client = mock.MagicMock(spec=ImapClient)
    client.list_folders.side_effect = ImapError("connection lost")

    with pytest.raises(ImapError, match="connection lost"):
        cross_folder_resolve(client, "<msg@example.com>")


def test_cross_folder_resolve_source_folder_first() -> None:
    """With source_folder, the message is found there → short-circuits."""
    client = mock.MagicMock(spec=ImapClient)
    client.list_folders.return_value = [
        MailboxInfo(name="INBOX", attributes=(), delimiter="/"),
        MailboxInfo(name="Archive", attributes=(), delimiter="/"),
        MailboxInfo(name="Sent", attributes=(), delimiter="/"),
    ]
    client.select_folder.return_value = 5
    # Message is found in source_folder (INBOX) under a new UID.
    client.search_uids.side_effect = [
        [99],  # source_folder (INBOX) — found with new UID
    ]

    result = cross_folder_resolve(client, "<test@example.com>", source_folder="INBOX")

    # Must return immediately — no other folders searched.
    assert result == ("INBOX", 99)
    client.select_folder.assert_called_once_with("INBOX")
    client.search_uids.assert_called_once_with('HEADER Message-ID "<test@example.com>"')
    # list_folders() is never called because source_folder check returned early.
    client.list_folders.assert_not_called()


def test_cross_folder_resolve_source_folder_not_found_falls_through() -> None:
    """Message absent from source_folder; falls through and finds it elsewhere."""
    client = mock.MagicMock(spec=ImapClient)
    client.list_folders.return_value = [
        MailboxInfo(name="INBOX", attributes=(), delimiter="/"),
        MailboxInfo(name="Archive", attributes=(), delimiter="/"),
        MailboxInfo(name="Sent", attributes=(), delimiter="/"),
    ]
    client.select_folder.return_value = 5
    client.search_uids.side_effect = [
        [],  # source_folder (INBOX) — not found
        [7],  # Archive — found
    ]

    result = cross_folder_resolve(client, "<msg@example.com>", source_folder="INBOX")

    assert result == ("Archive", 7)
    # INBOX is selected twice: once for the explicit check, skipped in the loop.
    assert client.select_folder.call_args_list == [
        mock.call("INBOX"),
        mock.call("Archive"),
    ]
    # list_folders was called (after source_folder fallthrough).
    client.list_folders.assert_called_once()


def test_cross_folder_resolve_source_folder_imap_error_falls_through() -> None:
    """source_folder SELECT raises ImapError → falls through to full search."""
    client = mock.MagicMock(spec=ImapClient)
    client.list_folders.return_value = [
        MailboxInfo(name="Archive", attributes=(), delimiter="/"),
        MailboxInfo(name="Sent", attributes=(), delimiter="/"),
    ]
    client.select_folder.return_value = 5
    # source_folder SELECT fails; full search finds it in Archive.
    client.search_uids.side_effect = [
        [7],  # Archive
    ]

    def _select_side_effect(folder_name: str) -> int:
        if folder_name == "INBOX":
            raise ImapError("folder deleted")
        return 5

    client.select_folder.side_effect = _select_side_effect

    result = cross_folder_resolve(client, "<msg@example.com>", source_folder="INBOX")

    assert result == ("Archive", 7)
    # INBOX was attempted (and failed), then Archive was tried.
    assert client.select_folder.call_args_list == [
        mock.call("INBOX"),
        mock.call("Archive"),
    ]


def test_cross_folder_resolve_source_folder_none_backward_compat() -> None:
    """source_folder=None (default) — existing behaviour unchanged."""
    client = mock.MagicMock(spec=ImapClient)
    client.list_folders.return_value = [
        MailboxInfo(name="INBOX", attributes=(), delimiter="/"),
        MailboxInfo(name="Projects", attributes=(), delimiter="/"),
    ]
    client.select_folder.return_value = 5
    client.search_uids.side_effect = [
        [],  # INBOX
        [3],  # Projects
    ]

    # No source_folder argument — backward-compatible default.
    result = cross_folder_resolve(client, "<m@example.com>")

    assert result == ("Projects", 3)
    # Both folders searched (no early source_folder check).
    assert client.select_folder.call_count == 2


# ---------------------------------------------------------------------------
# _copyuid_indicates_empty_source
# ---------------------------------------------------------------------------


def test_copyuid_indicates_empty_source_true() -> None:
    """_copyuid_indicates_empty_source returns True when COPYUID source-set is empty."""
    data = [b"1 OK [COPYUID 12345 "]
    assert ImapClient._copyuid_indicates_empty_source(data) is True
