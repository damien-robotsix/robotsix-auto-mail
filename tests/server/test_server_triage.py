"""Tests for triage operations (POST /run-triage)."""

from __future__ import annotations

from tests.server.conftest import (
    _populate_db,
    _post_form,
    _post_to_path,
    _seed_triage_decision,
    _start_test_server,
)

from robotsix_auto_mail.db import set_watermark

# ---------------------------------------------------------------------------
# POST /run-triage tests
# ---------------------------------------------------------------------------


def test_run_triage_no_untriaged_redirects(single_db: str) -> None:
    """POST /run-triage returns 302 when all records already have triage
    decisions (no untriaged records → no LLM call needed)."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "triaged-msg",
                "sender": "x@x.com",
                "subject": "Already triaged",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "triaged-msg", action="TO_ARCHIVE")

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/run-triage", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()


def test_run_triage_no_api_key_returns_302_and_watermark_clears(
    single_db: str,
) -> None:
    """POST /run-triage now launches a background thread and always redirects
    to /board (302).  When untriaged records exist but no API key is
    configured, the background thread fails, but the watermark is always
    cleared by the finally block.  Poll the watermark until it transitions
    away from 'running', asserting it eventually clears.
    """
    import time

    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db

    _populate_db(
        single_db,
        [
            {
                "message_id": "untriaged-msg",
                "sender": "x@x.com",
                "subject": "Untriaged mail",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    # No triage_decisions row → record is untriaged.

    server, port = _start_test_server(single_db)
    try:
        # POST /run-triage → always redirects to /board (302).
        resp = _post_to_path(port, "/run-triage", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"

        # Poll the watermark until the background thread clears it.
        deadline = time.monotonic() + 10
        state = None
        while time.monotonic() < deadline:
            conn = _init_db(single_db, skip_migrations=True)
            try:
                state = get_watermark(conn, "triage_run:state")
            finally:
                conn.close()
            if state != "running":
                break
            time.sleep(0.1)
        assert state == "idle", f"Watermark didn't clear after 10 s: {state!r}"
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# New tests: triage-running indicator and background execution
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# New tests: triage-running indicator and background execution
# ---------------------------------------------------------------------------


def test_run_triage_already_running(single_db: str) -> None:
    """POST /run-triage when triage is already running is idempotent —
    it redirects to /board without spawning a second thread."""
    from robotsix_auto_mail.db import init_db as _init_db

    # Seed the watermark as "running" before starting the server.
    conn = _init_db(single_db)
    try:
        set_watermark(conn, "triage_run:state", "running")
    finally:
        conn.close()

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/run-triage", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"

        # Watermark should still be "running" (no thread cleared it).
        conn2 = _init_db(single_db)
        try:
            from robotsix_auto_mail.db import get_watermark

            assert get_watermark(conn2, "triage_run:state") == "running"
        finally:
            conn2.close()
    finally:
        server.shutdown()


def test_run_triage_background_clears_watermark(single_db: str) -> None:
    """When triage completes (fast path — no untriaged records), the
    background thread clears the watermark back to 'idle'."""
    import time

    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db

    # Populate a record that already has a triage decision so the
    # agent finds zero untriaged records and returns immediately.
    _populate_db(
        single_db,
        [
            {
                "message_id": "triaged-msg",
                "sender": "x@x.com",
                "subject": "Already triaged",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "triaged-msg", action="TO_ARCHIVE")

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/run-triage", {})
        assert resp.status == 302

        # Poll until the watermark clears (should be fast — no LLM call).
        deadline = time.monotonic() + 5
        state = None
        while time.monotonic() < deadline:
            conn = _init_db(single_db, skip_migrations=True)
            try:
                state = get_watermark(conn, "triage_run:state")
            finally:
                conn.close()
            if state != "running":
                break
            time.sleep(0.05)
        assert state == "idle", f"Watermark didn't clear: {state!r}"
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# POST /force-triage-column tests
# ---------------------------------------------------------------------------


def test_force_triage_column_valid_action_deletes_and_redirects(
    single_db: str,
) -> None:
    """POST /force-triage-column with a valid action deletes matching
    triage decisions and redirects to /board."""
    from robotsix_auto_mail.db import init_db as _init_db

    _populate_db(
        single_db,
        [
            {
                "message_id": "msg-1",
                "sender": "x@x.com",
                "subject": "One",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "msg-1", action="TO_ARCHIVE")

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(
            port, "/force-triage-column", {"action": "TO_ARCHIVE"}
        )
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"

        # Verify the triage decision was deleted.
        conn = _init_db(single_db, skip_migrations=True)
        try:
            cur = conn.execute(
                "SELECT COUNT(*) FROM triage_decisions WHERE action = ?",
                ("TO_ARCHIVE",),
            )
            assert cur.fetchone()[0] == 0
        finally:
            conn.close()
    finally:
        server.shutdown()


def test_force_triage_column_invalid_action_returns_400(
    single_db: str,
) -> None:
    """POST /force-triage-column with an invalid action returns 400."""
    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(
            port, {"action": "NONEXISTENT"}, path="/force-triage-column"
        )
        assert status == 400
        assert "Invalid triage action" in body
        assert "NONEXISTENT" in body
    finally:
        server.shutdown()


def test_force_triage_column_inbox_action_returns_400(
    single_db: str,
) -> None:
    """POST /force-triage-column with action='INBOX' raises TriageError,
    caught by the handler and returned as 400."""
    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(
            port, {"action": "INBOX"}, path="/force-triage-column"
        )
        assert status == 400
        assert "Cannot delete triage decisions for action='INBOX'" in body
    finally:
        server.shutdown()


def test_force_triage_column_generic_exception_returns_503(
    single_db: str,
) -> None:
    """POST /force-triage-column returns 503 JSON when an unexpected
    exception occurs during triage-decision deletion."""
    from unittest.mock import patch

    import robotsix_auto_mail.triage as triage_pkg

    _populate_db(
        single_db,
        [
            {
                "message_id": "msg-1",
                "sender": "x@x.com",
                "subject": "One",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        with patch.object(
            triage_pkg,
            "delete_triage_decisions_by_action",
            side_effect=RuntimeError("simulated DB failure"),
        ):
            status, body = _post_form(
                port, {"action": "TO_DELETE"}, path="/force-triage-column"
            )
        assert status == 503
        assert "simulated DB failure" in body
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Delete button on TO_DELETE cards
# ---------------------------------------------------------------------------
