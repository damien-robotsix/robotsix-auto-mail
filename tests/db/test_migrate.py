"""Tests for additive SQLite migration helpers and the legacy-DB path."""

from __future__ import annotations

import sqlite3

from robotsix_auto_mail.db import init_db
from robotsix_auto_mail.db._migrate import (
    add_column_if_missing,
    run_additive_migrations,
)

# A deliberately pre-additive ``mail_records`` schema: the minimal column
# set that existed before ``unsubscribe_header``/``notes``/``draft_text``/
# ``sent_reply_text``/``source_folder`` were added.
_LEGACY_MAIL_RECORDS = """
CREATE TABLE mail_records (
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
)
"""

_ADDITIVE_COLUMN_NAMES = (
    "unsubscribe_header",
    "notes",
    "draft_text",
    "sent_reply_text",
    "source_folder",
)


def _column_info(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    """Return ``{column_name: dflt_value}`` from ``PRAGMA table_info``."""
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1]: row[4] for row in cur.fetchall()}


def test_add_column_if_missing_adds_and_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")

    assert add_column_if_missing(conn, "t", "notes TEXT NOT NULL DEFAULT ''") is True
    assert "notes" in _column_info(conn, "t")

    # Second call: column already exists -> False, no raise.
    assert add_column_if_missing(conn, "t", "notes TEXT NOT NULL DEFAULT ''") is False
    conn.close()


def test_add_column_if_missing_returns_false_when_present() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, notes TEXT)")

    assert add_column_if_missing(conn, "t", "notes TEXT") is False
    conn.close()


def test_run_additive_migrations_adds_all_and_is_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_LEGACY_MAIL_RECORDS)

    columns = (
        "unsubscribe_header TEXT NOT NULL DEFAULT ''",
        "notes TEXT NOT NULL DEFAULT ''",
        "draft_text TEXT NOT NULL DEFAULT ''",
        "sent_reply_text TEXT NOT NULL DEFAULT ''",
        "source_folder TEXT NOT NULL DEFAULT 'INBOX'",
    )
    run_additive_migrations(conn, "mail_records", columns)
    info = _column_info(conn, "mail_records")
    for name in _ADDITIVE_COLUMN_NAMES:
        assert name in info

    # Idempotent on a second run.
    run_additive_migrations(conn, "mail_records", columns)
    assert _column_info(conn, "mail_records").keys() == info.keys()
    conn.close()


def test_init_db_upgrades_legacy_schema(tmp_db_path: str) -> None:
    """Legacy DB missing all five additive columns is upgraded by init_db."""
    seed = sqlite3.connect(tmp_db_path)
    seed.executescript(_LEGACY_MAIL_RECORDS)
    seed.commit()
    seed.close()

    conn = init_db(tmp_db_path)
    info = _column_info(conn, "mail_records")
    expected_defaults = {
        "unsubscribe_header": "''",
        "notes": "''",
        "draft_text": "''",
        "sent_reply_text": "''",
        "source_folder": "'INBOX'",
    }
    for name, default in expected_defaults.items():
        assert name in info, f"missing additive column {name}"
        assert info[name] == default
    conn.close()
