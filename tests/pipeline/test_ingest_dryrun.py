"""Tests for ingest_mail dry_run mode."""

from __future__ import annotations

import sqlite3
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import (
    MailRecord,
    get_watermark,
)
from robotsix_auto_mail.pipeline import (
    ingest_mail,
)
from robotsix_auto_mail.pipeline._parse import ParseError
from tests.pipeline._helpers import _make_raw_message, _mock_imap_client


# ---------------------------------------------------------------------------
# ingest_mail - dry_run mode
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_dry_run_does_not_store_or_update_watermark(
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """dry_run=True fetches and parses but never calls
    insert_record or update_watermark."""
    mock_fetch.return_value = [
        (1, _make_raw_message(message_id="<a@x>", subject="One")),
        (2, _make_raw_message(message_id="<b@x>", subject="Two")),
        (3, _make_raw_message(message_id="<c@x>", subject="Three")),
    ]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg, dry_run=True)

    # All three would have been stored (record_exists returns False).
    assert result.total_fetched == 3
    assert result.stored == 3
    assert result.skipped == 0
    assert result.errors == []

    # Watermark must NOT be updated.
    assert get_watermark(conn, "imap_uid") is None

    # No rows in DB.
    cur = conn.execute("SELECT COUNT(*) FROM mail_records")
    assert cur.fetchone()[0] == 0


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_dry_run_skips_duplicates(
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """dry_run=True still calls record_exists and counts duplicates as skipped."""
    # Pre-populate one message.
    from robotsix_auto_mail.db import insert_record

    rec = MailRecord(
        message_id="<existing@x>",
        sender="alice@x.com",
        subject="Old",
        date="2025-01-01",
    )
    insert_record(conn, rec)

    mock_fetch.return_value = [
        (1, _make_raw_message(message_id="<existing@x>")),
        (2, _make_raw_message(message_id="<new@x>")),
    ]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg, dry_run=True)

    assert result.total_fetched == 2
    assert result.stored == 1  # <new@x> would have been stored
    assert result.skipped == 1  # <existing@x> was already there
    assert result.errors == []

    # Still only 1 row (the pre-populated one).
    cur = conn.execute("SELECT COUNT(*) FROM mail_records")
    assert cur.fetchone()[0] == 1

    # Watermark untouched.
    assert get_watermark(conn, "imap_uid") is None

    # Existing row must be byte-for-byte unchanged — dry run must not UPDATE.
    row = conn.execute(
        "SELECT source_folder, imap_uid FROM mail_records WHERE message_id = ?",
        ("<existing@x>",),
    ).fetchone()
    assert row is not None
    assert (
        row[0] == "INBOX"
    )  # source_folder: default from MailRecord, must not be overwritten
    assert (
        row[1] is None
    )  # imap_uid: was None at insert; must not be overwritten to uid=1


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
@mock.patch("robotsix_auto_mail.pipeline.parse_message")
def test_ingest_dry_run_parses_messages(
    mock_parse: mock.MagicMock,
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """dry_run=True still parses messages; parse errors are collected."""
    r1 = _make_raw_message(message_id="<good@x>")
    r2 = b"invalid mime message"

    mock_fetch.return_value = [(1, r1), (2, r2)]
    imap = _mock_imap_client()

    # Let the real parser handle r1; fail on r2.
    from robotsix_auto_mail.pipeline._parse import parse_message as real_parse

    def side_effect(
        raw_bytes: bytes,
        *,
        imap_uid: int | None = None,
        source_folder: str = "INBOX",
    ) -> MailRecord:
        if raw_bytes == r2:
            raise ParseError("failed to parse raw bytes as MIME message")
        return real_parse(raw_bytes, imap_uid=imap_uid, source_folder=source_folder)

    mock_parse.side_effect = side_effect

    result = ingest_mail(conn, imap, cfg, dry_run=True)

    assert result.total_fetched == 2
    assert result.stored == 1  # <good@x> would have been stored
    assert result.skipped == 0
    assert len(result.errors) == 1
    assert result.errors[0].uid == 2
    assert "failed to parse" in result.errors[0].error
