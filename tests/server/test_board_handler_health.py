"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

import json
import sqlite3
from urllib.request import urlopen

from tests.server.conftest import (
    _populate_db,
    _post_form,
    _start_test_server,
)

# ===========================================================================
# GET /health tests
# ===========================================================================


def test_health_valid_db_returns_200(single_db: str) -> None:
    """GET /health with a valid DB returns 200 and {"status": "ok"}."""
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/health")
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "application/json" in content_type
        body = resp.read().decode("utf-8")
        payload = json.loads(body)
        assert payload == {"status": "ok"}
    finally:
        server.shutdown()


def test_health_missing_db_returns_200() -> None:
    """GET /health with a missing/corrupt DB still returns 200 (liveness-only)."""
    server, port = _start_test_server("/dev/null/nonexistent.db")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/health")
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "application/json" in content_type
        body = resp.read().decode("utf-8")
        payload = json.loads(body)
        assert payload == {"status": "ok"}
    finally:
        server.shutdown()


def _downgrade_triage_check_to_legacy(db_path: str) -> None:
    """Replace ``triage_decisions`` with a pre-``TO_CALENDAR`` CHECK constraint.

    Simulates a DB created before ``TO_CALENDAR`` joined the triage
    vocabulary.  Rows are preserved.  Runs with raw sqlite3 so no migration
    heals the stale constraint.
    """
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            PRAGMA foreign_keys=OFF;
            CREATE TABLE triage_decisions_legacy (
                message_id  TEXT NOT NULL UNIQUE,
                action      TEXT NOT NULL CHECK(action IN (
                    'DRAFT_READY', 'HUMAN_TRIAGE', 'INBOX', 'PENDING_ACTION',
                    'TO_ANSWER', 'TO_ARCHIVE', 'TO_DELETE'
                )),
                source      TEXT NOT NULL,
                reason      TEXT NOT NULL DEFAULT '',
                confidence  TEXT NOT NULL DEFAULT 'medium',
                updated_at  TEXT NOT NULL,
                FOREIGN KEY (message_id) REFERENCES mail_records(message_id)
            );
            INSERT INTO triage_decisions_legacy
                SELECT message_id, action, source, reason, confidence, updated_at
                FROM triage_decisions;
            DROP TABLE triage_decisions;
            ALTER TABLE triage_decisions_legacy RENAME TO triage_decisions;
            """
        )
        conn.commit()
    finally:
        conn.close()


def test_move_to_calendar_on_legacy_db_does_not_500(single_db: str) -> None:
    """A /move to TO_CALENDAR on a legacy-constraint DB returns a clean response.

    The board move path opens the DB with ``skip_migrations=True``, so the
    stale CHECK constraint persists at runtime and ``set_triage_decision``
    raises ``sqlite3.IntegrityError``.  Defense-in-depth must turn that into
    a normal HTTP response (not 500/502) instead of crashing the worker.
    """
    _populate_db(
        single_db,
        [
            {
                "message_id": "cal-me",
                "sender": "x@x.com",
                "subject": "Calendar test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "Meet on 2025-02-01",
                "status": "to_read",
            },
        ],
    )
    _downgrade_triage_check_to_legacy(single_db)

    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(
            port,
            {"message_id": "cal-me", "triage_action": "TO_CALENDAR"},
        )
        assert status not in (500, 502), f"got {status}: {body}"
    finally:
        server.shutdown()


def test_health_content_type_is_json() -> None:
    """GET /health response Content-Type is application/json."""
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/health")
        content_type = resp.headers.get("Content-Type", "")
        assert "application/json" in content_type
    finally:
        server.shutdown()
