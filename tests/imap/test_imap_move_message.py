"""Tests for IMAP move_message, delete_messages, and move_messages operations."""

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
# delete_messages / move_messages (batched primitives)
# ---------------------------------------------------------------------------


def test_delete_messages_single_chunk(cfg: MailConfig) -> None:
    """delete_messages issues one STORE over the UID set + one EXPUNGE."""
    mock_ssl = _make_mock_imap_ssl()

    def _uid_side_effect(*args: object, **kwargs: object) -> tuple[str, list[bytes]]:
        if args[0] == "SEARCH":
            return ("OK", [b"1 2 3"])
        return ("OK", [b""])

    mock_ssl.uid.side_effect = _uid_side_effect
    mock_ssl.expunge.return_value = ("OK", [b""])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.delete_messages([1, 2, 3])

    store_calls = [c for c in mock_ssl.uid.call_args_list if c.args[0] == "STORE"]
    assert store_calls == [mock.call("STORE", "1,2,3", "+FLAGS", "(\\Deleted)")]
    mock_ssl.expunge.assert_called_once()


def test_delete_messages_empty_is_noop(cfg: MailConfig) -> None:
    """delete_messages on an empty list issues no IMAP calls."""
    mock_ssl = _make_mock_imap_ssl()

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.delete_messages([])

    mock_ssl.uid.assert_not_called()
    mock_ssl.expunge.assert_not_called()


def test_delete_messages_chunks_in_hundreds(cfg: MailConfig) -> None:
    """delete_messages issues one STORE + one EXPUNGE per <=100-UID chunk."""
    mock_ssl = _make_mock_imap_ssl()

    def _uid_side_effect(*args: object, **kwargs: object) -> tuple[str, list[bytes]]:
        if args[0] == "SEARCH":
            # Return all searched UIDs as present (space-separated).
            uid_str = str(args[1]).replace("UID ", "").replace(",", " ")
            return ("OK", [uid_str.encode()])
        return ("OK", [b""])

    mock_ssl.uid.side_effect = _uid_side_effect
    mock_ssl.expunge.return_value = ("OK", [b""])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.delete_messages(list(range(1, 251)))

    store_calls = [c for c in mock_ssl.uid.call_args_list if c.args[0] == "STORE"]
    assert len(store_calls) == 3  # 100 + 100 + 50
    assert mock_ssl.expunge.call_count == 3
    # First chunk packs UIDs 1..100 into a single comma-joined set.
    assert store_calls[0].args[1] == ",".join(str(u) for u in range(1, 101))


def test_delete_messages_store_fails_raises(cfg: MailConfig) -> None:
    """delete_messages raises ImapError when UID STORE returns non-OK."""
    mock_ssl = _make_mock_imap_ssl()

    def _uid_side_effect(*args: object, **kwargs: object) -> tuple[str, list[bytes]]:
        if args[0] == "SEARCH":
            return ("OK", [b"7 8"])  # both UIDs exist, so STORE is attempted
        return ("NO", [b"err"])

    mock_ssl.uid.side_effect = _uid_side_effect

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapError, match="UID STORE"):
                client.delete_messages([7, 8])

    mock_ssl.expunge.assert_not_called()


def test_move_messages_copies_then_deletes(cfg: MailConfig) -> None:
    """move_messages issues one UID COPY over the set then the batched delete."""
    mock_ssl = _make_mock_imap_ssl()

    def _uid_side_effect(*args: object, **kwargs: object) -> tuple[str, list[bytes]]:
        if args[0] == "SEARCH":
            return ("OK", [b"3 5 9"])
        return ("OK", [b""])

    mock_ssl.uid.side_effect = _uid_side_effect
    mock_ssl.expunge.return_value = ("OK", [b""])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.move_messages([3, 5, 9], "Archive/2026")

    copy_calls = [c for c in mock_ssl.uid.call_args_list if c.args[0] == "COPY"]
    store_calls = [c for c in mock_ssl.uid.call_args_list if c.args[0] == "STORE"]
    assert copy_calls == [mock.call("COPY", "3,5,9", "Archive/2026")]
    assert store_calls == [mock.call("STORE", "3,5,9", "+FLAGS", "(\\Deleted)")]
    mock_ssl.expunge.assert_called_once()


def test_move_messages_empty_is_noop(cfg: MailConfig) -> None:
    """move_messages on an empty list issues no IMAP calls."""
    mock_ssl = _make_mock_imap_ssl()

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.move_messages([], "Archive")

    mock_ssl.uid.assert_not_called()
    mock_ssl.expunge.assert_not_called()


# ---------------------------------------------------------------------------
# move_message (single-message primitive)
# ---------------------------------------------------------------------------


def test_move_message_success(cfg: MailConfig) -> None:
    """move_message pre-verifies, COPYs, then deletes the original."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.side_effect = _uid_side_effect(
        search_result=("OK", [b"42"]),
        other=("OK", [b"[COPYUID 1 42 99]"]),
    )
    mock_ssl.expunge.return_value = ("OK", [b""])

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            client.move_message(42, "Archive")

    mock_ssl.uid.assert_any_call("COPY", "42", "Archive")
    mock_ssl.uid.assert_any_call("STORE", "42", "+FLAGS", "(\\Deleted)")
    mock_ssl.expunge.assert_called_once()


def test_move_message_uid_not_found_raises(cfg: MailConfig) -> None:
    """move_message raises when the UID is absent; no COPY issued."""
    mock_ssl = _make_mock_imap_ssl()
    mock_ssl.uid.side_effect = _uid_side_effect(
        search_result=("OK", [b""]),
        other=("OK", [b""]),
    )

    with mock.patch("imaplib.IMAP4_SSL", return_value=mock_ssl):
        with ImapClient(cfg) as client:
            with pytest.raises(ImapMessageNotFoundError, match="42"):
                client.move_message(42, "Archive")

    mock_ssl.uid.assert_called_once_with("SEARCH", "UID 42")
    for call in mock_ssl.uid.call_args_list:
        assert call.args[0] != "COPY"
