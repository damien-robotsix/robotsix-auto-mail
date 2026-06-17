"""Tests for reconciliation operations (POST /reconcile and background runner)."""

from __future__ import annotations

from tests.server.conftest import (
    _populate_db,
    _post_to_path,
    _start_test_server,
)

from robotsix_auto_mail.db import set_watermark

# ---------------------------------------------------------------------------
# POST /reconcile tests
# ---------------------------------------------------------------------------


def test_reconcile_endpoint_redirects(single_db: str) -> None:
    """POST /reconcile returns 302 with Location: /board."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "test-msg",
                "sender": "x@x.com",
                "subject": "Test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/reconcile", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()


def test_reconcile_endpoint_idempotent(single_db: str) -> None:
    """POST /reconcile is a no-op (302) when reconcile:state is already 'running'."""
    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db

    # Pre-set reconcile:state to "running".
    conn = _init_db(single_db)
    try:
        set_watermark(conn, "reconcile:state", "running")
    finally:
        conn.close()

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/reconcile", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"

        # Watermark should still be "running" (no-op, no thread spawned).
        conn2 = _init_db(single_db)
        try:
            assert get_watermark(conn2, "reconcile:state") == "running"
        finally:
            conn2.close()
    finally:
        server.shutdown()


def test_reconcile_background_sets_and_clears_watermark(single_db: str) -> None:
    """_run_reconcile_background with mail_config=None transitions 'running' → 'idle'."""
    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.server.adapters import _run_reconcile_background

    _init_db(single_db).close()

    _run_reconcile_background(single_db, None)

    conn = _init_db(single_db)
    try:
        state = get_watermark(conn, "reconcile:state")
        # After returning, watermark must be "idle".
        assert state == "idle", f"Expected 'idle', got {state!r}"
    finally:
        conn.close()


def test_reconcile_background_no_mail_config(single_db: str) -> None:
    """_run_reconcile_background(db_path, None) returns cleanly, watermark is 'idle'."""
    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.server.adapters import _run_reconcile_background

    _init_db(single_db).close()

    # Should not raise.
    _run_reconcile_background(single_db, None)

    conn = _init_db(single_db)
    try:
        state = get_watermark(conn, "reconcile:state")
        assert state == "idle"
    finally:
        conn.close()
