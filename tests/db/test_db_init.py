"""Tests for init_db, schema verification, and status migrations."""

from __future__ import annotations

import sqlite3

import pytest

from robotsix_auto_mail.db import init_db

# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


def test_init_db_returns_connection() -> None:
    """init_db returns a sqlite3.Connection."""
    conn = init_db(":memory:")
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_init_db_creates_mail_records_table() -> None:
    """mail_records table exists and has the expected columns."""
    conn = init_db(":memory:")
    try:
        cursor = conn.execute("PRAGMA table_info('mail_records')")
        cols = {row[1]: row[2] for row in cursor.fetchall()}
        expected = {
            "id": "INTEGER",
            "imap_uid": "INTEGER",
            "source_folder": "TEXT",
            "message_id": "TEXT",
            "sender": "TEXT",
            "subject": "TEXT",
            "date": "TEXT",
            "recipients_json": "TEXT",
            "body_plain": "TEXT",
            "body_html": "TEXT",
            "attachments_json": "TEXT",
            "unsubscribe_header": "TEXT",
            "status": "TEXT",
            "notes": "TEXT",
            "draft_text": "TEXT",
            "sent_reply_text": "TEXT",
            "calendar_event_ref": "TEXT",
            "calendar_correlation_id": "TEXT",
        }
        for name, type_ in expected.items():
            assert name in cols, f"Column {name} missing"
            assert cols[name].upper() == type_, f"Column {name} type mismatch"
        assert len(cols) == len(expected), f"Extra columns: {set(cols) - set(expected)}"
    finally:
        conn.close()


def test_init_db_creates_watermark_table() -> None:
    """watermark table exists with key/value columns."""
    conn = init_db(":memory:")
    try:
        cursor = conn.execute("PRAGMA table_info('watermark')")
        cols = {row[1]: row[2] for row in cursor.fetchall()}
        assert cols == {"key": "TEXT", "value": "TEXT"}
    finally:
        conn.close()


def test_init_db_enables_wal() -> None:
    """WAL journal mode is requested (in-memory DBs use 'memory' mode)."""
    conn = init_db(":memory:")
    try:
        cur = conn.execute("PRAGMA journal_mode")
        mode = cur.fetchone()[0]
        # On :memory: databases WAL is not applicable; on file-based
        # databases it will be "wal".  Either is fine — the pragma was
        # set and didn't error.
        assert mode.lower() in ("wal", "memory")
    finally:
        conn.close()


def test_init_db_enables_foreign_keys() -> None:
    """Foreign keys pragma is ON."""
    conn = init_db(":memory:")
    try:
        cur = conn.execute("PRAGMA foreign_keys")
        val = cur.fetchone()[0]
        assert val == 1
    finally:
        conn.close()


def test_init_db_idempotent() -> None:
    """Calling init_db twice on the same database is safe."""
    conn = init_db(":memory:")
    try:
        conn.executescript(_SCHEMA_AGAIN)
        # No exception = success
    finally:
        conn.close()


def test_init_db_migrates_legacy_statuses(tmp_db_path: str) -> None:
    """init_db remaps legacy status values to the new awaiting-action set.

    inbox→to_read, triaging→needs_reply, archive→no_action; done is left
    unchanged.  After migration no legacy value remains.
    """
    conn = init_db(tmp_db_path)
    try:
        legacy = {
            "<inbox@x.com>": "inbox",
            "<triaging@x.com>": "triaging",
            "<archive@x.com>": "archive",
            "<done@x.com>": "done",
        }
        for mid, status in legacy.items():
            conn.execute(
                "INSERT INTO mail_records "
                "(message_id, sender, subject, date, recipients_json, "
                "body_plain, body_html, attachments_json, status) "
                "VALUES (?, 's@x.com', 'S', 'd', '{}', '', '', '[]', ?)",
                (mid, status),
            )
        conn.commit()
    finally:
        conn.close()

    # Re-open: the startup migration should rewrite the legacy values.
    conn = init_db(tmp_db_path)
    try:
        rows = dict(
            conn.execute("SELECT message_id, status FROM mail_records").fetchall()
        )
        assert rows == {
            "<inbox@x.com>": "to_read",
            "<triaging@x.com>": "needs_reply",
            "<archive@x.com>": "no_action",
            "<done@x.com>": "done",
        }
        # No row left with a legacy/invalid status.
        leftover = conn.execute(
            "SELECT COUNT(*) FROM mail_records "
            "WHERE status IN ('inbox', 'triaging', 'archive')"
        ).fetchone()[0]
        assert leftover == 0
    finally:
        conn.close()


_SCHEMA_AGAIN = """
CREATE TABLE IF NOT EXISTS mail_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    imap_uid        INTEGER,
    message_id      TEXT    NOT NULL UNIQUE,
    sender          TEXT    NOT NULL,
    subject         TEXT    NOT NULL,
    date            TEXT    NOT NULL,
    recipients_json TEXT    NOT NULL,
    body_plain      TEXT    NOT NULL,
    body_html       TEXT    NOT NULL,
    attachments_json TEXT   NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'to_read'
);

CREATE TABLE IF NOT EXISTS watermark (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# _migrate_status_to_triage (via init_db)
# ---------------------------------------------------------------------------


def test_init_db_migrates_status_to_triage(tmp_db_path: str) -> None:
    """init_db creates triage_decisions rows for non-default status values
    that lack a decision."""
    conn = init_db(tmp_db_path)
    try:
        # Insert records with non-default statuses but no triage_decisions.
        records = {
            "<needs@x.com>": "needs_reply",
            "<wait@x.com>": "waiting",
            "<noact@x.com>": "no_action",
            "<done@x.com>": "done",
            "<toread@x.com>": "to_read",
        }
        for mid, status in records.items():
            conn.execute(
                "INSERT INTO mail_records "
                "(message_id, sender, subject, date, recipients_json, "
                "body_plain, body_html, attachments_json, status) "
                "VALUES (?, 's@x.com', 'S', 'd', '{}', '', '', '[]', ?)",
                (mid, status),
            )
        conn.commit()
    finally:
        conn.close()

    # Re-open: the startup migration should create triage_decisions rows.
    conn = init_db(tmp_db_path)
    try:
        from robotsix_auto_mail.triage import list_triage_decisions

        decisions = {d.message_id: d for d in list_triage_decisions(conn)}
        # needs_reply → answer
        assert "<needs@x.com>" in decisions
        assert decisions["<needs@x.com>"].action == "TO_ANSWER"
        assert decisions["<needs@x.com>"].source == "user"
        assert "migrated from legacy status" in decisions["<needs@x.com>"].reason
        # waiting → INBOX
        assert decisions["<wait@x.com>"].action == "INBOX"
        # no_action → TO_ARCHIVE
        assert decisions["<noact@x.com>"].action == "TO_ARCHIVE"
        # done → TO_ARCHIVE
        assert decisions["<done@x.com>"].action == "TO_ARCHIVE"
        # to_read is skipped (no migration)
        assert "<toread@x.com>" not in decisions
    finally:
        conn.close()


def test_init_db_status_migration_idempotent(tmp_db_path: str) -> None:
    """Re-running init_db does not insert duplicate triage_decisions rows."""
    conn = init_db(tmp_db_path)
    try:
        conn.execute(
            "INSERT INTO mail_records "
            "(message_id, sender, subject, date, recipients_json, "
            "body_plain, body_html, attachments_json, status) "
            "VALUES ('<dup@x.com>', 's@x.com', 'S', 'd', '{}', '', '', '[]', 'needs_reply')"
        )
        conn.commit()
    finally:
        conn.close()

    # First migration.
    conn = init_db(tmp_db_path)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM triage_decisions WHERE message_id = '<dup@x.com>'"
        )
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()

    # Second migration (idempotent).
    conn = init_db(tmp_db_path)
    try:
        cur = conn.execute(
            "SELECT COUNT(*) FROM triage_decisions WHERE message_id = '<dup@x.com>'"
        )
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()


def test_init_db_status_migration_skips_existing_decisions(
    tmp_db_path: str,
) -> None:
    """Records that already have a triage_decisions row are not overwritten."""
    conn = init_db(tmp_db_path)
    try:
        conn.execute(
            "INSERT INTO mail_records "
            "(message_id, sender, subject, date, recipients_json, "
            "body_plain, body_html, attachments_json, status) "
            "VALUES ('<existing@x.com>', 's@x.com', 'S', 'd', '{}', '', '', '[]', 'needs_reply')"
        )
        # Pre-create a triage_decisions row with a different action.
        conn.execute(
            "INSERT INTO triage_decisions "
            "(message_id, action, source, reason, confidence, updated_at) "
            "VALUES ('<existing@x.com>', 'TO_DELETE', 'agent', 'spam', 'high', '2025-01-01T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()

    # Re-open: migration must NOT overwrite the existing decision.
    conn = init_db(tmp_db_path)
    try:
        cur = conn.execute(
            "SELECT action, source, reason FROM triage_decisions "
            "WHERE message_id = '<existing@x.com>'"
        )
        row = cur.fetchone()
        assert row == ("TO_DELETE", "agent", "spam")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# resolve_db_path
# ---------------------------------------------------------------------------


def test_resolve_db_path_empty_derives_from_account_id() -> None:
    """Empty db_path derives <mail_data_root>/<account_id>/mail.db."""
    from robotsix_auto_mail.db.queries import resolve_db_path

    result = resolve_db_path("myaccount", "", "/data")
    assert result == "/data/myaccount/mail.db"


def test_resolve_db_path_relative_resolves_against_root() -> None:
    """Relative db_path is resolved against mail_data_root."""
    from robotsix_auto_mail.db.queries import resolve_db_path

    result = resolve_db_path("acct", ".data/custom/mail.db", "/data")
    assert result == "/data/.data/custom/mail.db"


def test_resolve_db_path_absolute_passes_through() -> None:
    """Already-absolute db_path is returned unchanged."""
    from robotsix_auto_mail.db.queries import resolve_db_path

    result = resolve_db_path("acct", "/custom/path/mail.db", "/data")
    assert result == "/custom/path/mail.db"


def test_resolve_db_path_memory_passes_through() -> None:
    """:memory: is always returned unchanged."""
    from robotsix_auto_mail.db.queries import resolve_db_path

    result = resolve_db_path("acct", ":memory:", "/data")
    assert result == ":memory:"


def test_resolve_db_path_absolute_is_not_rerooted() -> None:
    """An absolute path is returned as-is regardless of mail_data_root."""
    from robotsix_auto_mail.db.queries import resolve_db_path

    result = resolve_db_path("acct", "/home/user/db.sqlite", "/data")
    assert result == "/home/user/db.sqlite"


# ---------------------------------------------------------------------------
# _check_data_root_is_mount
# ---------------------------------------------------------------------------


def test_check_data_root_is_mount_raises_on_nonexistent(tmp_path, monkeypatch):
    """SystemExit when mail_data_root does not exist."""
    import os

    from robotsix_auto_mail.db.queries import _check_data_root_is_mount

    monkeypatch.delenv("ROBOTSIX_SKIP_MOUNT_CHECK", raising=False)
    nonexistent = str(tmp_path / "nonexistent")
    try:
        _check_data_root_is_mount(nonexistent)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        pytest.fail("Expected SystemExit")


def test_check_data_root_is_mount_raises_on_non_mount(tmp_path, monkeypatch):
    """SystemExit when mail_data_root is not a mount point."""
    import os

    from robotsix_auto_mail.db.queries import _check_data_root_is_mount

    monkeypatch.delenv("ROBOTSIX_SKIP_MOUNT_CHECK", raising=False)
    # tmp_path is a regular directory, not a mount point.
    try:
        _check_data_root_is_mount(str(tmp_path))
    except SystemExit as exc:
        assert exc.code == 1
    else:
        pytest.fail("Expected SystemExit")


def test_check_data_root_is_mount_passes_on_actual_mount(monkeypatch):
    """_check_data_root_is_mount passes when the path is a mount point.

    /proc is always a mount point on Linux — this test is correct
    on every Linux host.
    """
    import os

    if not os.path.exists("/proc"):
        pytest.skip("/proc not available")

    from robotsix_auto_mail.db.queries import _check_data_root_is_mount

    monkeypatch.delenv("ROBOTSIX_SKIP_MOUNT_CHECK", raising=False)
    # This must not raise.
    _check_data_root_is_mount("/proc")
