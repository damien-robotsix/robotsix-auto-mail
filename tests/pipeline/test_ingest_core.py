"""Tests for ingest_mail core behaviour — happy path, idempotency,
partial failures, edge cases, and module boundaries."""

from __future__ import annotations

import logging
import sqlite3
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import (
    get_watermark,
    MailRecord,
)
from robotsix_auto_mail.pipeline import (
    ingest_mail,
    update_watermark,
)
from robotsix_auto_mail.pipeline._parse import ParseError
from tests.pipeline._helpers import _make_raw_message, _mock_imap_client


# ---------------------------------------------------------------------------
# ingest_mail - happy path (acceptance criterion 1)
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_stores_three_messages_and_updates_watermark(
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """3 raw messages → stored=3, skipped=0, errors=[], watermark=max uid."""
    mock_fetch.return_value = [
        (1, _make_raw_message(message_id="<a@x>", subject="One")),
        (3, _make_raw_message(message_id="<b@x>", subject="Two")),
        (5, _make_raw_message(message_id="<c@x>", subject="Three")),
    ]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert result.total_fetched == 3
    assert result.stored == 3
    assert result.skipped == 0
    assert result.errors == []

    # Watermark must be the max UID (5).
    assert get_watermark(conn, "imap_uid") == "5"

    # All three rows should be in the DB.
    cur = conn.execute("SELECT COUNT(*) FROM mail_records")
    assert cur.fetchone()[0] == 3


# ---------------------------------------------------------------------------
# ingest_mail - batch_summary carries run duration
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_batch_summary_has_duration_ms(
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """batch_summary log record and IngestResult both expose a non-negative duration."""
    mock_fetch.return_value = [
        (1, _make_raw_message(message_id="<a@x>", subject="One")),
    ]
    imap = _mock_imap_client()

    with caplog.at_level(logging.INFO, logger="robotsix_auto_mail.pipeline"):
        result = ingest_mail(conn, imap, cfg)

    summaries = [r for r in caplog.records if r.message.startswith("batch_summary")]
    assert len(summaries) == 1
    assert "duration_ms=" in summaries[0].message
    assert "total_fetched=" in summaries[0].message

    assert isinstance(result.duration_ms, (int, float))
    assert result.duration_ms >= 0


# ---------------------------------------------------------------------------
# ingest_mail - idempotency (acceptance criterion 2)
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_idempotent_second_run_skips_all(
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """First run stores 3; second run (same data) stores 0, skips 3."""
    messages = [
        (1, _make_raw_message(message_id="<a@x>")),
        (2, _make_raw_message(message_id="<b@x>")),
        (3, _make_raw_message(message_id="<c@x>")),
    ]
    imap = _mock_imap_client()

    # First run.
    mock_fetch.return_value = messages
    r1 = ingest_mail(conn, imap, cfg)
    assert r1.stored == 3
    assert r1.skipped == 0
    assert get_watermark(conn, "imap_uid") == "3"

    # Second run — same data returned by fetch (simulating crash before
    # watermark update on first run, or just testing idempotency).
    r2 = ingest_mail(conn, imap, cfg)
    assert r2.stored == 0
    assert r2.skipped == 3
    # Watermark still 3 (re-updated to same value).
    assert get_watermark(conn, "imap_uid") == "3"

    # Only 3 rows total.
    cur = conn.execute("SELECT COUNT(*) FROM mail_records")
    assert cur.fetchone()[0] == 3


# ---------------------------------------------------------------------------
# ingest_mail - partial parse failure (acceptance criterion 3)
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
@mock.patch("robotsix_auto_mail.pipeline.parse_message")
def test_ingest_partial_parse_failure(
    mock_parse: mock.MagicMock,
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """5 messages, message #3 fails parse → stored=4, errors=1."""
    # Build real records for the good messages.
    r1 = _make_raw_message(message_id="<m1@x>")
    r2 = _make_raw_message(message_id="<m2@x>")
    r3 = b"garbage"  # will be intercepted by mock
    r4 = _make_raw_message(message_id="<m4@x>")
    r5 = _make_raw_message(message_id="<m5@x>")

    mock_fetch.return_value = [(1, r1), (2, r2), (3, r3), (4, r4), (5, r5)]

    # Let the real parse_message handle good messages; only fail on UID 3.
    from robotsix_auto_mail.pipeline._parse import parse_message as real_parse

    def side_effect(
        raw_bytes: bytes,
        *,
        imap_uid: int | None = None,
        source_folder: str = "INBOX",
    ) -> MailRecord:
        if raw_bytes == r3:
            raise ParseError("failed to parse raw bytes as MIME message")
        return real_parse(raw_bytes, imap_uid=imap_uid, source_folder=source_folder)

    mock_parse.side_effect = side_effect

    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert result.total_fetched == 5
    assert result.stored == 4
    assert result.skipped == 0
    assert len(result.errors) == 1

    err = result.errors[0]
    assert err.uid == 3
    assert err.message_id == ""
    assert "failed to parse" in err.error

    # Watermark advances past the failed UID.
    assert get_watermark(conn, "imap_uid") == "5"

    # Verify the stored messages match expected.
    cur = conn.execute("SELECT imap_uid FROM mail_records ORDER BY imap_uid")
    stored_uids = [row[0] for row in cur.fetchall()]
    assert stored_uids == [1, 2, 4, 5]


# ---------------------------------------------------------------------------
# ingest_mail - crash simulation (acceptance criterion 4)
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_crash_before_watermark_no_duplicates(
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """Simulate crash by calling pipeline, then re-calling with same data."""
    messages = [
        (10, _make_raw_message(message_id="<dup1@x>")),
        (11, _make_raw_message(message_id="<dup2@x>")),
    ]
    imap = _mock_imap_client()

    # "Crash" scenario: store messages but don't update watermark.
    # We simulate this by calling ingest_mail with a patched
    # update_watermark that is a no-op on the first call.
    with mock.patch("robotsix_auto_mail.pipeline.update_watermark") as mock_update:
        # First: update_watermark does nothing (crash simulation).
        mock_update.side_effect = lambda c, u: None

        mock_fetch.return_value = messages
        r1 = ingest_mail(conn, imap, cfg)
        assert r1.stored == 2
        assert r1.skipped == 0
        # Watermark was not persisted.
        assert get_watermark(conn, "imap_uid") is None

    # "Re-run" after crash: same fetch result, watermark still None.
    mock_fetch.return_value = messages
    r2 = ingest_mail(conn, imap, cfg)
    assert r2.stored == 0
    assert r2.skipped == 2
    assert get_watermark(conn, "imap_uid") == "11"

    # No duplicate rows.
    cur = conn.execute("SELECT COUNT(*) FROM mail_records")
    assert cur.fetchone()[0] == 2


# ---------------------------------------------------------------------------
# ingest_mail - empty batch
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_empty_batch(
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """Empty fetch → all zeros, watermark untouched."""
    mock_fetch.return_value = []
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert result.total_fetched == 0
    assert result.stored == 0
    assert result.skipped == 0
    assert result.errors == []

    # Watermark unchanged (was never set).
    assert get_watermark(conn, "imap_uid") is None


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_empty_batch_does_not_touch_existing_watermark(
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """Empty batch leaves an existing watermark alone."""
    update_watermark(conn, 42)

    mock_fetch.return_value = []
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert result.total_fetched == 0
    assert get_watermark(conn, "imap_uid") == "42"


# ---------------------------------------------------------------------------
# ingest_mail - DB insert failure
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
@mock.patch("robotsix_auto_mail.pipeline.insert_record")
def test_ingest_insert_failure_is_collected(
    mock_insert: mock.MagicMock,
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """insert_record raises an exception → error collected, others still stored."""
    msg_ok1 = _make_raw_message(message_id="<ok1@x>")
    msg_bad = _make_raw_message(message_id="<bad@x>")
    msg_ok2 = _make_raw_message(message_id="<ok2@x>")

    mock_fetch.return_value = [(1, msg_ok1), (2, msg_bad), (3, msg_ok2)]
    imap = _mock_imap_client()

    # Only the middle insert fails.
    def side_effect(c: sqlite3.Connection, r: MailRecord) -> int | None:
        if r.message_id == "<bad@x>":
            raise sqlite3.DatabaseError("disk I/O error")
        # Use the real insert_record.
        from robotsix_auto_mail.db import insert_record as real_insert

        return real_insert(c, r)

    mock_insert.side_effect = side_effect

    result = ingest_mail(conn, imap, cfg)

    assert result.total_fetched == 3
    assert result.stored == 2
    assert result.skipped == 0
    assert len(result.errors) == 1
    assert result.errors[0].uid == 2
    assert result.errors[0].message_id == "<bad@x>"
    assert "disk I/O error" in result.errors[0].error

    # Watermark still advances.
    assert get_watermark(conn, "imap_uid") == "3"


# ---------------------------------------------------------------------------
# ingest_mail - record_exists dance
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_record_exists_skips(
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """Pre-populate DB with a message, then re-feed it — counted as skipped."""
    # Pre-populate one message directly.
    from robotsix_auto_mail.db import insert_record

    rec = MailRecord(
        message_id="<existing@x>",
        sender="alice@x.com",
        subject="Old",
        date="2025-01-01T00:00:00",
        imap_uid=5,
    )
    insert_record(conn, rec)

    # Now feed two messages — one duplicate, one new.
    mock_fetch.return_value = [
        (6, _make_raw_message(message_id="<existing@x>")),
        (7, _make_raw_message(message_id="<new@x>")),
    ]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert result.total_fetched == 2
    assert result.stored == 1
    assert result.skipped == 1
    assert result.errors == []

    # Only 2 total rows (the pre-existing one + the new one).
    cur = conn.execute("SELECT COUNT(*) FROM mail_records")
    assert cur.fetchone()[0] == 2

    # Watermark at max UID (7).
    assert get_watermark(conn, "imap_uid") == "7"


# ---------------------------------------------------------------------------
# ingest_mail - watermark advances to max UID in batch
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_watermark_advances_to_max_uid(
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """Watermark is set to the highest UID in the batch, even with skips."""
    # Pre-populate to make some messages get skipped.
    from robotsix_auto_mail.db import insert_record

    rec = MailRecord(
        message_id="<skip@x>",
        sender="a@x.com",
        subject="Skip",
        date="2025-01-01",
        imap_uid=44,
    )
    insert_record(conn, rec)

    mock_fetch.return_value = [
        (42, _make_raw_message(message_id="<m42@x>")),
        (44, _make_raw_message(message_id="<skip@x>")),  # will be skipped
        (45, _make_raw_message(message_id="<m45@x>")),
    ]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert result.stored == 2
    assert result.skipped == 1
    # Watermark is 45, not 42 or 44.
    assert get_watermark(conn, "imap_uid") == "45"


# ---------------------------------------------------------------------------
# ingest_mail - ParseError with non-empty message
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
@mock.patch("robotsix_auto_mail.pipeline.parse_message")
def test_ingest_parse_error_message(
    mock_parse: mock.MagicMock,
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """ParseError returns a human-readable error string."""
    mock_fetch.return_value = [(1, b"valid raw bytes")]
    mock_parse.side_effect = ParseError("failed to parse raw bytes as MIME message")
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert len(result.errors) == 1
    assert "failed to parse" in result.errors[0].error


# ---------------------------------------------------------------------------
# ingest_mail - mixing stored, skipped, errors in one batch
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
@mock.patch("robotsix_auto_mail.pipeline.parse_message")
def test_ingest_mixed_store_skip_error(
    mock_parse: mock.MagicMock,
    mock_fetch: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """Batch with new, duplicate, and unparseable messages."""
    # Pre-populate one.
    from robotsix_auto_mail.db import insert_record

    rec = MailRecord(
        message_id="<dup@x>",
        sender="s@x.com",
        subject="Dup",
        date="2025-01-01",
    )
    insert_record(conn, rec)

    r10 = _make_raw_message(message_id="<new@x>")
    r11 = _make_raw_message(message_id="<dup@x>")  # duplicate
    r12 = b"garbage bytes not mime at all"
    r13 = _make_raw_message(message_id="<new2@x>")

    mock_fetch.return_value = [(10, r10), (11, r11), (12, r12), (13, r13)]

    from robotsix_auto_mail.pipeline._parse import parse_message as real_parse

    def side_effect(
        raw_bytes: bytes,
        *,
        imap_uid: int | None = None,
        source_folder: str = "INBOX",
    ) -> MailRecord:
        if raw_bytes == r12:
            raise ParseError("failed to parse raw bytes as MIME message")
        return real_parse(raw_bytes, imap_uid=imap_uid, source_folder=source_folder)

    mock_parse.side_effect = side_effect

    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert result.total_fetched == 4
    assert result.stored == 2
    assert result.skipped == 1
    assert len(result.errors) == 1
    assert result.errors[0].uid == 12
    assert get_watermark(conn, "imap_uid") == "13"


# ---------------------------------------------------------------------------
# Module boundary tests
# ---------------------------------------------------------------------------


def test_pipeline_imports_from_expected_modules() -> None:
    """pipeline.py imports from db, imap, parser, config."""
    import robotsix_auto_mail.pipeline as mod

    source = mod.__file__
    assert source is not None
    content = open(source).read()
    assert "from robotsix_auto_mail.db import" in content
    assert "from robotsix_auto_mail.imap import" in content
    assert "from robotsix_auto_mail.pipeline._parse import" in content
    assert "from robotsix_auto_mail.config import" in content
    # Must not import smtp_client.
    assert "from robotsix_auto_mail.smtp import" not in content
