"""Tests for basic delete and batch delete operations."""

from __future__ import annotations

from urllib.request import urlopen

from robotsix_auto_mail.db import get_record_by_message_id, init_db
from tests.server.conftest_helpers import (
    _populate_db,
    _post_to_path,
    _seed_triage_decision,
    _start_test_server,
    _wait_for_batch_idle,
)

# ---------------------------------------------------------------------------
# Delete button on TO_DELETE cards
# ---------------------------------------------------------------------------


def test_delete_success_removes_record_and_redirects(single_db: str) -> None:
    """POST /delete with valid message_id deletes the record and returns 302."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "del-me",
                "sender": "x@x.com",
                "subject": "Delete test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "del-me", action="TO_DELETE")

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/delete", {"message_id": "del-me"})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()

    # Verify record is gone from the DB.
    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "del-me") is None
    finally:
        conn.close()

    # Verify the board no longer shows the card.
    server2, port2 = _start_test_server(single_db)
    try:
        resp2 = urlopen(f"http://127.0.0.1:{port2}/board")
        board_html = resp2.read().decode("utf-8")
        assert "del-me" not in board_html
        assert "x@x.com" not in board_html
    finally:
        server2.shutdown()


def test_delete_missing_message_id_returns_400() -> None:
    """POST /delete without message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/delete", {})
        assert resp.status == 400
    finally:
        server.shutdown()


def test_delete_empty_message_id_returns_400() -> None:
    """POST /delete with empty message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/delete", {"message_id": "  "})
        assert resp.status == 400
    finally:
        server.shutdown()


def test_delete_unknown_message_id_returns_404() -> None:
    """POST /delete with nonexistent message_id returns 404."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(
            port,
            "/delete",
            {"message_id": "does-not-exist"},
        )
        assert resp.status == 404
    finally:
        server.shutdown()


def test_batch_delete_success_removes_all_to_delete_records_and_redirects(
    single_db: str,
) -> None:
    """POST /batch-delete deletes every TO_DELETE record and redirects 302."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "bd-del-1",
                "sender": "a@b.com",
                "subject": "Delete me 1",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
            {
                "message_id": "bd-del-2",
                "sender": "c@d.com",
                "subject": "Delete me 2",
                "date": "2025-01-02T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
            {
                "message_id": "bd-keep",
                "sender": "e@f.com",
                "subject": "Keep me",
                "date": "2025-01-03T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "bd-del-1", action="TO_DELETE")
    _seed_triage_decision(single_db, "bd-del-2", action="TO_DELETE")
    # bd-keep is untriaged → INBOX, should survive.

    server, port = _start_test_server(single_db)
    try:
        resp = _post_to_path(port, "/batch-delete", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
        # The worker now runs in a background daemon thread — poll the
        # batch_op:state watermark until it clears back to "idle".
        _wait_for_batch_idle(single_db)
    finally:
        server.shutdown()

    # Verify the TO_DELETE records are gone.
    conn = init_db(single_db)
    try:
        assert get_record_by_message_id(conn, "bd-del-1") is None
        assert get_record_by_message_id(conn, "bd-del-2") is None
        assert get_record_by_message_id(conn, "bd-keep") is not None
    finally:
        conn.close()


def test_batch_delete_empty_column_returns_302() -> None:
    """POST /batch-delete when TO_DELETE is empty → 302, no error."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/batch-delete", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()
