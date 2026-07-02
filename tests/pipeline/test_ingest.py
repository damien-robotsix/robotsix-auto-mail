"""Tests for the ingest_mail pipeline function."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import FrozenInstanceError
from unittest import mock

import pytest
from tests.pipeline._helpers import _make_raw_message, _mock_imap_client

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import (
    MailRecord,
    get_watermark,
)
from robotsix_auto_mail.pipeline import (
    IngestError,
    IngestResult,
    ingest_mail,
    update_watermark,
)
from robotsix_auto_mail.pipeline._parse import ParseError

# ---------------------------------------------------------------------------
# IngestError / IngestResult dataclass tests
# ---------------------------------------------------------------------------


def test_ingest_error_is_frozen() -> None:
    err = IngestError(uid=1, message_id="<x@y>", error="boom")
    assert err.uid == 1
    assert err.message_id == "<x@y>"
    assert err.error == "boom"
    with pytest.raises(FrozenInstanceError):
        err.uid = 2  # type: ignore[misc]


def test_ingest_error_empty_message_id() -> None:
    err = IngestError(uid=5, message_id="", error="parse failed")
    assert err.message_id == ""


def test_ingest_result_is_frozen() -> None:
    result = IngestResult(total_fetched=3, stored=2, skipped=1, errors=[])
    assert result.total_fetched == 3
    assert result.stored == 2
    assert result.skipped == 1
    assert result.errors == []
    with pytest.raises(FrozenInstanceError):
        result.stored = 99  # type: ignore[misc]


def test_ingest_result_defaults() -> None:
    result = IngestResult(total_fetched=0, stored=0, skipped=0, errors=[])
    assert result.total_fetched == 0
    assert result.errors == []


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


# ---------------------------------------------------------------------------
# ingest_mail - first-run archive setup
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_calls_setup_archive_before_fetch(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """A normal run calls setup_archive exactly once, before fetching."""
    manager = mock.Mock()
    manager.attach_mock(mock_setup_archive, "setup_archive")
    manager.attach_mock(mock_fetch, "fetch_new_messages")
    mock_fetch.return_value = []

    imap = _mock_imap_client()
    ingest_mail(conn, imap, cfg)

    mock_setup_archive.assert_called_once_with(
        conn,
        imap,
        archive_root=cfg.archive_root,
        api_key=cfg.llm_api_key.get_secret_value(),
        provider_model=cfg.llm_provider_model,
    )
    # setup_archive must run before fetch_new_messages.
    call_order = [c[0] for c in manager.mock_calls]
    assert call_order.index("setup_archive") < call_order.index("fetch_new_messages")


@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_dry_run_does_not_call_setup_archive(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """dry_run=True must not call setup_archive."""
    mock_fetch.return_value = []
    imap = _mock_imap_client()

    ingest_mail(conn, imap, cfg, dry_run=True)

    mock_setup_archive.assert_not_called()


@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_archive_disabled_does_not_call_setup_archive(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """archive_enabled=False must skip setup_archive entirely."""
    cfg_disabled = cfg.model_copy(update={"archive_enabled": False})
    mock_fetch.return_value = []
    imap = _mock_imap_client()

    ingest_mail(conn, imap, cfg_disabled)

    mock_setup_archive.assert_not_called()


@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_passes_configured_archive_root(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """The configured archive_root is forwarded to setup_archive."""
    cfg_custom = cfg.model_copy(update={"archive_root": "custom-archive"})
    mock_fetch.return_value = []
    imap = _mock_imap_client()

    ingest_mail(conn, imap, cfg_custom)

    mock_setup_archive.assert_called_once_with(
        conn,
        imap,
        archive_root="custom-archive",
        api_key=cfg.llm_api_key.get_secret_value(),
        provider_model=cfg.llm_provider_model,
    )


@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_setup_archive_failure_does_not_propagate(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """An exception from setup_archive is swallowed; ingestion continues."""
    mock_setup_archive.side_effect = RuntimeError("LLM exploded")
    mock_fetch.return_value = [
        (1, _make_raw_message(message_id="<a@x>")),
    ]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert isinstance(result, IngestResult)
    assert result.total_fetched == 1
    assert result.stored == 1


# ---------------------------------------------------------------------------
# ingest_mail - post-ingest triage pass
# ---------------------------------------------------------------------------


@mock.patch("robotsix_auto_mail.pipeline.run_triage_agent")
@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_runs_triage_on_new_mail(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    mock_triage: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """A normal run triages only-undecided mail and reports the count."""
    mock_fetch.return_value = [(1, _make_raw_message(message_id="<a@x>"))]
    mock_triage.return_value = [object(), object()]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    mock_triage.assert_called_once_with(
        conn,
        api_key=cfg.llm_api_key.get_secret_value(),
        provider_model=cfg.llm_provider_model,
        only_undecided=True,
        user_email=cfg.username,
        rules_path=mock.ANY,
    )
    assert result.triaged == 2
    # Triage must perform no IMAP/mailbox action of its own.
    imap.assert_not_called()


@mock.patch("robotsix_auto_mail.pipeline.run_triage_agent")
@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_triage_disabled_does_not_call_triage(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    mock_triage: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """triage_on_ingest=False must skip run_triage_agent entirely."""
    cfg_disabled = cfg.model_copy(update={"triage_on_ingest": False})
    mock_fetch.return_value = [(1, _make_raw_message(message_id="<a@x>"))]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg_disabled)

    mock_triage.assert_not_called()
    assert result.triaged == 0


@mock.patch("robotsix_auto_mail.pipeline.run_triage_agent")
@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_dry_run_does_not_call_triage(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    mock_triage: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """dry_run=True must not call run_triage_agent."""
    mock_fetch.return_value = [(1, _make_raw_message(message_id="<a@x>"))]
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg, dry_run=True)

    mock_triage.assert_not_called()
    assert result.triaged == 0


@mock.patch("robotsix_auto_mail.pipeline.run_triage_agent")
@mock.patch("robotsix_auto_mail.pipeline.setup_archive")
@mock.patch("robotsix_auto_mail.pipeline.fetch_new_messages")
def test_ingest_triage_failure_does_not_propagate(
    mock_fetch: mock.MagicMock,
    mock_setup_archive: mock.MagicMock,
    mock_triage: mock.MagicMock,
    conn: sqlite3.Connection,
    cfg: MailConfig,
) -> None:
    """A triage exception is swallowed; ingestion still returns triaged=0."""
    from robotsix_auto_mail.triage import TriageError

    mock_fetch.return_value = [(1, _make_raw_message(message_id="<a@x>"))]
    mock_triage.side_effect = TriageError("LLM exploded")
    imap = _mock_imap_client()

    result = ingest_mail(conn, imap, cfg)

    assert isinstance(result, IngestResult)
    assert result.total_fetched == 1
    assert result.stored == 1
    assert result.triaged == 0
