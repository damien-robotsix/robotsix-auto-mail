"""Tests for fetch_new_messages and update_watermark."""

from __future__ import annotations

import sqlite3
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import get_watermark, set_watermark
from robotsix_auto_mail.pipeline import fetch_new_messages, update_watermark
from tests.pipeline._helpers import _mock_imap_client

# ---------------------------------------------------------------------------
# Inlined fetch tests — fetch_new_messages / update_watermark (from former
# fetch.py, now part of pipeline.py)
# ---------------------------------------------------------------------------


def test_update_watermark_sets_value(conn: sqlite3.Connection) -> None:
    """update_watermark persists the UID so get_watermark can retrieve it."""
    update_watermark(conn, 99)
    assert get_watermark(conn, "imap_uid") == "99"


def test_update_watermark_upserts(conn: sqlite3.Connection) -> None:
    """Calling update_watermark twice with different UIDs updates the value."""
    update_watermark(conn, 1)
    update_watermark(conn, 42)
    assert get_watermark(conn, "imap_uid") == "42"

    # Only one row.
    cur = conn.execute(
        "SELECT COUNT(*) FROM watermark WHERE key = ?",
        ("imap_uid",),
    )
    assert cur.fetchone()[0] == 1


def test_fetch_new_messages_first_run(
    conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """With no watermark, searches ALL and fetches everything."""
    client = _mock_imap_client()
    client.search_uids.return_value = [1, 2]
    client.fetch_messages.return_value = [
        (1, b"msg1"),
        (2, b"msg2"),
    ]

    result = fetch_new_messages(conn, client, cfg)

    client.select_folder.assert_called_once_with("INBOX")
    client.search_uids.assert_called_once_with("ALL")
    client.fetch_messages.assert_called_once_with([1, 2])
    assert result == [(1, b"msg1"), (2, b"msg2")]


def test_fetch_new_messages_first_run_empty_mailbox(
    conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """With no watermark and an empty mailbox, returns [] without FETCH."""
    client = _mock_imap_client()
    client.search_uids.return_value = []

    result = fetch_new_messages(conn, client, cfg)

    assert result == []
    client.fetch_messages.assert_not_called()


def test_fetch_new_messages_incremental(
    conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """Watermark is 1; search finds [1,2,3]; returns only [2,3]."""
    set_watermark(conn, "imap_uid", "1")

    client = _mock_imap_client()
    client.search_uids.return_value = [1, 2, 3]
    client.fetch_messages.return_value = [
        (2, b"msg2"),
        (3, b"msg3"),
    ]

    result = fetch_new_messages(conn, client, cfg)

    client.search_uids.assert_called_once_with("UID 1:*")
    client.fetch_messages.assert_called_once_with([2, 3])
    assert result == [(2, b"msg2"), (3, b"msg3")]


def test_fetch_new_messages_incremental_watermark_filtered(
    conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """Watermark UID is excluded even when server returns it."""
    set_watermark(conn, "imap_uid", "5")

    client = _mock_imap_client()
    client.search_uids.return_value = [5, 6]
    client.fetch_messages.return_value = [(6, b"msg6")]

    result = fetch_new_messages(conn, client, cfg)

    # UID 5 (the watermark itself) must be filtered out.
    client.fetch_messages.assert_called_once_with([6])
    assert result == [(6, b"msg6")]


def test_fetch_new_messages_no_new_messages(
    conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """Watermark is 42; search returns only [42] → no FETCH, empty result."""
    set_watermark(conn, "imap_uid", "42")

    client = _mock_imap_client()
    client.search_uids.return_value = [42]

    result = fetch_new_messages(conn, client, cfg)

    assert result == []
    client.fetch_messages.assert_not_called()


def test_fetch_new_messages_uses_configured_folder(
    conn: sqlite3.Connection,
) -> None:
    """fetch_new_messages selects config.imap_folder, not hardcoded INBOX."""
    cfg = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="u",
        password="p",
        imap_folder="Archive",
    )

    client = _mock_imap_client()
    client.search_uids.return_value = [10]
    client.fetch_messages.return_value = [(10, b"body")]

    fetch_new_messages(conn, client, cfg)

    client.select_folder.assert_called_once_with("Archive")


def test_fetch_new_messages_default_folder_is_inbox(
    conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """With the default config, selects INBOX."""
    client = _mock_imap_client()
    client.search_uids.return_value = [1]
    client.fetch_messages.return_value = [(1, b"x")]

    fetch_new_messages(conn, client, cfg)

    client.select_folder.assert_called_once_with("INBOX")


def test_fetch_new_messages_does_not_update_watermark(
    conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """fetch_new_messages reads the watermark but does NOT write it."""
    set_watermark(conn, "imap_uid", "5")

    client = _mock_imap_client()
    client.search_uids.return_value = [6, 7]
    client.fetch_messages.return_value = [(6, b"m6"), (7, b"m7")]

    fetch_new_messages(conn, client, cfg)

    # Watermark must remain "5" — unchanged.
    assert get_watermark(conn, "imap_uid") == "5"


def test_fetch_new_messages_does_not_commit(
    conn: sqlite3.Connection, cfg: MailConfig
) -> None:
    """fetch_new_messages does not commit — it's read-only on the DB."""
    # Set a watermark so we exercise the incremental path.
    set_watermark(conn, "imap_uid", "3")

    # Verify by checking fetch_new_messages calls get_watermark but
    # never calls set_watermark or conn.commit (aside from
    # set_watermark in this test's own setup).
    with mock.patch(
        "robotsix_auto_mail.pipeline.set_watermark", wraps=set_watermark
    ) as mock_set:
        with mock.patch(
            "robotsix_auto_mail.pipeline.get_watermark", wraps=get_watermark
        ) as mock_get:
            client = _mock_imap_client()
            client.search_uids.return_value = [4]
            client.fetch_messages.return_value = [(4, b"data")]

            fetch_new_messages(conn, client, cfg)

            # fetch_new_messages reads the watermark...
            mock_get.assert_called()
            # ... but must NOT write it.
            mock_set.assert_not_called()
