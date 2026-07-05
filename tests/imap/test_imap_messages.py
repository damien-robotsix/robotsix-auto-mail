"""Tests for IMAP message operations: search, fetch, delete, and move."""

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


def _uid_side_effect(
    *, search_result: tuple[str, list[bytes]], other: tuple[str, list[bytes]]
) -> object:
    """Build a ``.uid`` side_effect branching on the IMAP command.

    The destructive primitives now pre-verify a UID via ``UID SEARCH``
    before issuing ``STORE`` / ``COPY``.  This helper routes ``"SEARCH"``
    to *search_result* (UID-existence) and every other command (``STORE``,
    ``COPY``) to *other*.
    """

    def _side_effect(command: str, *args: object) -> tuple[str, list[bytes]]:
        if command == "SEARCH":
            return search_result
        return other

    return _side_effect


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
