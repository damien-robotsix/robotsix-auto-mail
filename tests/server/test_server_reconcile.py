"""Tests for reconciliation operations (POST /reconcile and background runner)."""

from __future__ import annotations

import os
import tempfile

from tests.server.conftest import (
    _populate_db,
    _post_to_path,
    _start_test_server,
)

from robotsix_auto_mail.db import set_watermark

# ---------------------------------------------------------------------------
# POST /reconcile tests
# ---------------------------------------------------------------------------


def test_reconcile_endpoint_redirects() -> None:
    """POST /reconcile returns 302 with Location: /board."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/reconcile", {})
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_reconcile_endpoint_idempotent() -> None:
    """POST /reconcile is a no-op (302) when reconcile:state is already 'running'."""
    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Pre-set reconcile:state to "running".
        conn = _init_db(db_path)
        try:
            set_watermark(conn, "reconcile:state", "running")
        finally:
            conn.close()

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/reconcile", {})
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"

            # Watermark should still be "running" (no-op, no thread spawned).
            conn2 = _init_db(db_path)
            try:
                assert get_watermark(conn2, "reconcile:state") == "running"
            finally:
                conn2.close()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_reconcile_background_sets_and_clears_watermark() -> None:
    """_run_reconcile_background with mail_config=None transitions 'running' → 'idle'."""
    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.server.adapters import _run_reconcile_background

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _init_db(db_path).close()

        _run_reconcile_background(db_path, None)

        conn = _init_db(db_path)
        try:
            state = get_watermark(conn, "reconcile:state")
            # After returning, watermark must be "idle".
            assert state == "idle", f"Expected 'idle', got {state!r}"
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_reconcile_background_no_mail_config() -> None:
    """_run_reconcile_background(db_path, None) returns cleanly, watermark is 'idle'."""
    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.server.adapters import _run_reconcile_background

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _init_db(db_path).close()

        # Should not raise.
        _run_reconcile_background(db_path, None)

        conn = _init_db(db_path)
        try:
            state = get_watermark(conn, "reconcile:state")
            assert state == "idle"
        finally:
            conn.close()
    finally:
        os.unlink(db_path)
