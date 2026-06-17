"""Tests for triage operations (POST /run-triage)."""

from __future__ import annotations

from tests.server.conftest import (
    _populate_db,
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
# Delete button on TO_DELETE cards
# ---------------------------------------------------------------------------
