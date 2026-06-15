"""Tests for triage operations (POST /run-triage)."""

from __future__ import annotations

import os
import tempfile

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


def test_run_triage_no_untriaged_redirects() -> None:
    """POST /run-triage returns 302 when all records already have triage
    decisions (no untriaged records → no LLM call needed)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        _seed_triage_decision(db_path, "triaged-msg", action="TO_ARCHIVE")

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/run-triage", {})
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_run_triage_no_api_key_returns_302_and_watermark_clears() -> None:
    """POST /run-triage now launches a background thread and always redirects
    to /board (302).  When untriaged records exist but no API key is
    configured, the background thread fails, but the watermark is always
    cleared by the finally block.  Poll the watermark until it transitions
    away from 'running', asserting it eventually clears.
    """
    import time

    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
        try:
            # POST /run-triage → always redirects to /board (302).
            resp = _post_to_path(port, "/run-triage", {})
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"

            # Poll the watermark until the background thread clears it.
            deadline = time.monotonic() + 10
            state = None
            while time.monotonic() < deadline:
                conn = _init_db(db_path, skip_migrations=True)
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
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# New tests: triage-running indicator and background execution
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# New tests: triage-running indicator and background execution
# ---------------------------------------------------------------------------


def test_run_triage_already_running() -> None:
    """POST /run-triage when triage is already running is idempotent —
    it redirects to /board without spawning a second thread."""
    from robotsix_auto_mail.db import init_db as _init_db

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Seed the watermark as "running" before starting the server.
        conn = _init_db(db_path)
        try:
            set_watermark(conn, "triage_run:state", "running")
        finally:
            conn.close()

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/run-triage", {})
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"

            # Watermark should still be "running" (no thread cleared it).
            conn2 = _init_db(db_path)
            try:
                from robotsix_auto_mail.db import get_watermark

                assert get_watermark(conn2, "triage_run:state") == "running"
            finally:
                conn2.close()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_run_triage_background_clears_watermark() -> None:
    """When triage completes (fast path — no untriaged records), the
    background thread clears the watermark back to 'idle'."""
    import time

    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Populate a record that already has a triage decision so the
        # agent finds zero untriaged records and returns immediately.
        _populate_db(
            db_path,
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
        _seed_triage_decision(db_path, "triaged-msg", action="TO_ARCHIVE")

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/run-triage", {})
            assert resp.status == 302

            # Poll until the watermark clears (should be fast — no LLM call).
            deadline = time.monotonic() + 5
            state = None
            while time.monotonic() < deadline:
                conn = _init_db(db_path, skip_migrations=True)
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
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Delete button on TO_DELETE cards
# ---------------------------------------------------------------------------
