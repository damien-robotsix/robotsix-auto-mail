"""Direct unit tests for the individual migration functions in
``robotsix_auto_mail.db._migrate``.

``tests/db/test_migrate.py`` exercises these only indirectly through the
``init_db`` wrapper.  These tests drive each function against an in-memory
SQLite database so idempotency, state transitions, the CHECK-constraint
rebuild, and transaction boundaries are covered on their own.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from robotsix_auto_mail.db._migrate import (
    _TRIAGE_ACTION_CHECK_VALUES,
    _migrate_draft_ready_to_answer,
    _migrate_legacy_statuses,
    _migrate_status_to_triage,
    _migrate_triage_action_check,
    _utc_now_iso,
)
from robotsix_auto_mail.db.models import VALID_TRIAGE_ACTIONS

# ---------------------------------------------------------------------------
# Minimal DDL fixtures (only the columns the migrations touch)
# ---------------------------------------------------------------------------

_MAIL_RECORDS_DDL = """
CREATE TABLE mail_records (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id TEXT NOT NULL UNIQUE,
    status     TEXT NOT NULL DEFAULT 'to_read'
)
"""

# Current triage_decisions DDL, with the live CHECK vocabulary.
_CURRENT_TRIAGE_DDL = f"""
CREATE TABLE triage_decisions (
    message_id  TEXT NOT NULL UNIQUE,
    action      TEXT NOT NULL CHECK(action IN (
                    {_TRIAGE_ACTION_CHECK_VALUES}
                )),
    source      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    confidence  TEXT NOT NULL DEFAULT 'medium',
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES mail_records(message_id)
)
"""

# A stale triage_decisions CHECK: predates ``TO_CALENDAR`` and still lists the
# retired ``DRAFT_READY`` action.  No FK so the draft-ready test can insert
# rows without a companion mail_records table.
_LEGACY_TRIAGE_DDL = """
CREATE TABLE triage_decisions (
    message_id  TEXT NOT NULL UNIQUE,
    action      TEXT NOT NULL CHECK(action IN (
                    'DRAFT_READY', 'HUMAN_TRIAGE', 'INBOX', 'PENDING_ACTION',
                    'TO_ANSWER', 'TO_ARCHIVE', 'TO_DELETE'
                )),
    source      TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT '',
    confidence  TEXT NOT NULL DEFAULT 'medium',
    updated_at  TEXT NOT NULL
)
"""


def _insert_decision(
    conn: sqlite3.Connection,
    message_id: str,
    action: str,
    *,
    source: str = "user",
    reason: str = "",
    confidence: str = "medium",
) -> None:
    conn.execute(
        "INSERT INTO triage_decisions "
        "(message_id, action, source, reason, confidence, updated_at) "
        "VALUES (?, ?, ?, ?, ?, '2025-01-01T00:00:00+00:00')",
        (message_id, action, source, reason, confidence),
    )


# ---------------------------------------------------------------------------
# _utc_now_iso
# ---------------------------------------------------------------------------


def test_utc_now_iso_is_parseable_utc() -> None:
    value = _utc_now_iso()
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# _migrate_legacy_statuses
# ---------------------------------------------------------------------------


def test_migrate_legacy_statuses_remaps_all_legacy_values() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_MAIL_RECORDS_DDL)
    conn.executemany(
        "INSERT INTO mail_records (message_id, status) VALUES (?, ?)",
        [
            ("a", "inbox"),
            ("b", "triaging"),
            ("c", "archive"),
            ("d", "to_read"),
            ("e", "needs_reply"),
        ],
    )
    conn.commit()

    _migrate_legacy_statuses(conn)

    statuses = dict(
        conn.execute("SELECT message_id, status FROM mail_records").fetchall()
    )
    assert statuses == {
        "a": "to_read",
        "b": "needs_reply",
        "c": "no_action",
        "d": "to_read",
        "e": "needs_reply",
    }
    conn.close()


def test_migrate_legacy_statuses_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_MAIL_RECORDS_DDL)
    conn.execute("INSERT INTO mail_records (message_id, status) VALUES ('a', 'inbox')")
    conn.commit()

    _migrate_legacy_statuses(conn)
    first = conn.execute(
        "SELECT status FROM mail_records WHERE message_id = 'a'"
    ).fetchone()[0]
    _migrate_legacy_statuses(conn)
    second = conn.execute(
        "SELECT status FROM mail_records WHERE message_id = 'a'"
    ).fetchone()[0]

    assert first == second == "to_read"
    conn.close()


# ---------------------------------------------------------------------------
# _migrate_status_to_triage
# ---------------------------------------------------------------------------


def _conn_with_mail_and_triage() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_MAIL_RECORDS_DDL)
    conn.executescript(_CURRENT_TRIAGE_DDL)
    return conn


def test_migrate_status_to_triage_inserts_mapped_decisions() -> None:
    conn = _conn_with_mail_and_triage()
    conn.executemany(
        "INSERT INTO mail_records (message_id, status) VALUES (?, ?)",
        [
            ("nr", "needs_reply"),
            ("w", "waiting"),
            ("na", "no_action"),
            ("dn", "done"),
            ("tr", "to_read"),
        ],
    )
    conn.commit()

    _migrate_status_to_triage(conn)

    actions = dict(
        conn.execute("SELECT message_id, action FROM triage_decisions").fetchall()
    )
    assert actions == {
        "nr": "TO_ANSWER",
        "w": "INBOX",
        "na": "TO_ARCHIVE",
        "dn": "TO_ARCHIVE",
    }
    # The default "to_read" status is never migrated.
    assert "tr" not in actions

    meta = conn.execute(
        "SELECT source, reason, confidence FROM triage_decisions "
        "WHERE message_id = 'nr'"
    ).fetchone()
    assert meta == ("user", "migrated from legacy status", "medium")
    conn.close()


def test_migrate_status_to_triage_skips_existing_decision() -> None:
    conn = _conn_with_mail_and_triage()
    conn.execute(
        "INSERT INTO mail_records (message_id, status) VALUES ('nr', 'needs_reply')"
    )
    _insert_decision(
        conn, "nr", "HUMAN_TRIAGE", reason="preexisting", confidence="high"
    )
    conn.commit()

    _migrate_status_to_triage(conn)

    row = conn.execute(
        "SELECT action, reason FROM triage_decisions WHERE message_id = 'nr'"
    ).fetchone()
    assert row == ("HUMAN_TRIAGE", "preexisting")
    conn.close()


def test_migrate_status_to_triage_idempotent() -> None:
    conn = _conn_with_mail_and_triage()
    conn.execute(
        "INSERT INTO mail_records (message_id, status) VALUES ('nr', 'needs_reply')"
    )
    conn.commit()

    _migrate_status_to_triage(conn)
    _migrate_status_to_triage(conn)

    count = conn.execute(
        "SELECT COUNT(*) FROM triage_decisions WHERE message_id = 'nr'"
    ).fetchone()[0]
    assert count == 1
    conn.close()


# ---------------------------------------------------------------------------
# _migrate_draft_ready_to_answer
# ---------------------------------------------------------------------------


def test_migrate_draft_ready_to_answer_remaps_and_leaves_others() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_LEGACY_TRIAGE_DDL)
    _insert_decision(conn, "m1", "DRAFT_READY", source="llm")
    _insert_decision(conn, "m2", "TO_ARCHIVE")
    conn.commit()

    _migrate_draft_ready_to_answer(conn)

    actions = dict(
        conn.execute("SELECT message_id, action FROM triage_decisions").fetchall()
    )
    assert actions == {"m1": "TO_ANSWER", "m2": "TO_ARCHIVE"}
    conn.close()


def test_migrate_draft_ready_to_answer_idempotent() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_LEGACY_TRIAGE_DDL)
    _insert_decision(conn, "m1", "DRAFT_READY")
    conn.commit()

    _migrate_draft_ready_to_answer(conn)
    _migrate_draft_ready_to_answer(conn)

    action = conn.execute(
        "SELECT action FROM triage_decisions WHERE message_id = 'm1'"
    ).fetchone()[0]
    assert action == "TO_ANSWER"
    conn.close()


# ---------------------------------------------------------------------------
# _migrate_triage_action_check
# ---------------------------------------------------------------------------


def test_migrate_triage_action_check_rebuilds_stale_constraint() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_MAIL_RECORDS_DDL)
    conn.executescript(_LEGACY_TRIAGE_DDL)
    conn.execute(
        "INSERT INTO mail_records (message_id, status) VALUES ('m1', 'to_read')"
    )
    _insert_decision(conn, "m1", "TO_ARCHIVE", reason="legacy")
    conn.commit()

    # The stale CHECK rejects the new action before the migration runs.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE triage_decisions SET action = 'TO_CALENDAR' WHERE message_id = 'm1'"
        )
    conn.rollback()

    _migrate_triage_action_check(conn)

    stored_ddl = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'triage_decisions'"
    ).fetchone()[0]
    for action in VALID_TRIAGE_ACTIONS:
        assert repr(action) in stored_ddl, f"CHECK missing {action}"

    # Pre-existing row copied across unchanged.
    row = conn.execute(
        "SELECT action, source, reason FROM triage_decisions WHERE message_id = 'm1'"
    ).fetchone()
    assert row == ("TO_ARCHIVE", "user", "legacy")

    # The previously-rejected action is now accepted.
    conn.execute(
        "UPDATE triage_decisions SET action = 'TO_CALENDAR' WHERE message_id = 'm1'"
    )
    conn.commit()
    assert (
        conn.execute(
            "SELECT action FROM triage_decisions WHERE message_id = 'm1'"
        ).fetchone()[0]
        == "TO_CALENDAR"
    )
    conn.close()


def test_migrate_triage_action_check_noop_when_current() -> None:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_MAIL_RECORDS_DDL)
    conn.executescript(_CURRENT_TRIAGE_DDL)
    conn.commit()

    before = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'triage_decisions'"
    ).fetchone()[0]
    _migrate_triage_action_check(conn)
    after = conn.execute(
        "SELECT sql FROM sqlite_master "
        "WHERE type = 'table' AND name = 'triage_decisions'"
    ).fetchone()[0]

    assert before == after
    conn.close()


def test_migrate_triage_action_check_missing_table_noop() -> None:
    conn = sqlite3.connect(":memory:")
    # No triage_decisions table exists — the function must return quietly.
    _migrate_triage_action_check(conn)
    conn.close()
