"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.request import urlopen

import pytest

if TYPE_CHECKING:
    pass

from tests.server.conftest import (
    _move_and_get_location,
    _populate_db,
    _post_form,
    _seed_triage_decision,
    _start_test_server,
)

from robotsix_auto_mail.db import init_db

# ---------------------------------------------------------------------------
# GET /email/{message_id}/status tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /email/{message_id}/status tests
# ---------------------------------------------------------------------------


def test_email_status_returns_200(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "<abc123@example.com>",
                "sender": "x@x.com",
                "subject": "Status test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "needs_reply",
            },
        ],
    )
    _seed_triage_decision(single_db, "<abc123@example.com>", action="TO_ANSWER")

    server, port = _start_test_server(single_db)
    try:
        import urllib.request

        encoded = urllib.request.pathname2url("<abc123@example.com>")
        resp = urlopen(f"http://127.0.0.1:{port}/email/{encoded}/status")
        assert resp.status == 200
        assert resp.headers.get("Content-Type", "").startswith("text/plain")
        body = resp.read().decode("utf-8")
        assert body == "TO_ANSWER"
    finally:
        server.shutdown()


def test_email_status_unknown_message_id_returns_404() -> None:
    server, port = _start_test_server(":memory:")
    try:
        import urllib.error

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/email/nonexistent/status")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()


def test_email_path_without_status_suffix_now_returns_detail(single_db: str) -> None:
    """GET /email/{mid} (no /status suffix) now returns the detail page."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "mid1",
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
        resp = urlopen(f"http://127.0.0.1:{port}/email/mid1")
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        assert "<!DOCTYPE html>" in body
        assert "Test" in body
        assert "x@x.com" in body
    finally:
        server.shutdown()


def test_handler_email_detail_returns_200(single_db: str) -> None:
    """GET /email/{encoded_id} returns 200 and HTML."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "<handler-detail@test.com>",
                "sender": "h@h.com",
                "subject": "Handler Detail",
                "date": "2025-01-01T00:00:00",
                "body_plain": "detail body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        import urllib.request

        encoded = urllib.request.pathname2url("<handler-detail@test.com>")
        resp = urlopen(f"http://127.0.0.1:{port}/email/{encoded}")
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "text/html" in content_type
        body = resp.read().decode("utf-8")
        assert "<!DOCTYPE html>" in body
        assert "Handler Detail" in body
    finally:
        server.shutdown()


def test_handler_email_detail_unknown_returns_404() -> None:
    """GET /email/unknown-id returns 404."""
    server, port = _start_test_server(":memory:")
    try:
        import urllib.error

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/email/does-not-exist")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()


def test_handler_email_detail_missing_db_returns_503() -> None:
    """GET /email/{id} returns 503 when DB is unavailable."""
    import urllib.error

    server, port = _start_test_server("/dev/null/nonexistent.db")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/email/anything")
        assert exc_info.value.code == 503
        body = exc_info.value.read().decode("utf-8")
        assert "Database unavailable" in body
    finally:
        server.shutdown()


def test_handler_email_detail_xss_prevention(single_db: str) -> None:
    """HTML in subject/body is escaped, not rendered on the detail page."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "<xss-detail@test.com>",
                "sender": "<script>alert(1)</script>",
                "subject": "<img onerror=alert(2)>",
                "date": "2025-01-01T00:00:00",
                "body_plain": "<b>evil body</b>",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        import urllib.request

        encoded = urllib.request.pathname2url("<xss-detail@test.com>")
        resp = urlopen(f"http://127.0.0.1:{port}/email/{encoded}")
        body = resp.read().decode("utf-8")

        # All angle brackets must be escaped
        assert "<script>" not in body
        assert "&lt;script&gt;" in body
        assert "&lt;img onerror" in body
        assert "&lt;b&gt;evil body&lt;/b&gt;" in body
    finally:
        server.shutdown()


def test_handler_email_detail_does_not_capture_status_route(single_db: str) -> None:
    """GET /email/{id}/status still returns plain text, not HTML detail."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "<status-route@test.com>",
                "sender": "s@s.com",
                "subject": "Status Route",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "done",
            },
        ],
    )
    _seed_triage_decision(single_db, "<status-route@test.com>", action="TO_ARCHIVE")

    server, port = _start_test_server(single_db)
    try:
        import urllib.request

        encoded = urllib.request.pathname2url("<status-route@test.com>")
        resp = urlopen(f"http://127.0.0.1:{port}/email/{encoded}/status")
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "text/plain" in content_type
        body = resp.read().decode("utf-8")
        # "done" migrates to triage action "TO_ARCHIVE".
        assert body == "TO_ARCHIVE"
        # Should NOT be HTML
        assert "<!DOCTYPE html>" not in body
    finally:
        server.shutdown()


def test_handler_email_detail_with_recipients(single_db: str) -> None:
    """Detail page shows To and CC when present."""
    conn = init_db(single_db)
    try:
        conn.execute(
            "INSERT INTO mail_records "
            "(message_id, sender, subject, date, recipients_json, "
            "body_plain, body_html, attachments_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, '', '[]', ?)",
            (
                "<with-cc@test.com>",
                "sender@test.com",
                "With CC",
                "2025-01-01T00:00:00",
                '{"to": ["alice@x.com", "bob@x.com"], "cc": ["carol@x.com"]}',
                "body",
                "to_read",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    server, port = _start_test_server(single_db)
    try:
        import urllib.request

        encoded = urllib.request.pathname2url("<with-cc@test.com>")
        resp = urlopen(f"http://127.0.0.1:{port}/email/{encoded}")
        body = resp.read().decode("utf-8")
        assert "alice@x.com, bob@x.com" in body
        assert "carol@x.com" in body
        assert ">CC</div>" in body
    finally:
        server.shutdown()


def test_handler_email_detail_with_attachments(single_db: str) -> None:
    """Detail page shows attachment filenames and sizes."""
    conn = init_db(single_db)
    try:
        conn.execute(
            "INSERT INTO mail_records "
            "(message_id, sender, subject, date, recipients_json, "
            "body_plain, body_html, attachments_json, status) "
            "VALUES (?, ?, ?, ?, '{}', ?, '', ?, ?)",
            (
                "<with-attach@test.com>",
                "sender@test.com",
                "With Attachments",
                "2025-01-01T00:00:00",
                "body",
                (
                    '[{"filename": "doc.pdf", "size": 2048}, '
                    '{"filename": "img.png", "size": 512}]'
                ),
                "to_read",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    server, port = _start_test_server(single_db)
    try:
        import urllib.request

        encoded = urllib.request.pathname2url("<with-attach@test.com>")
        resp = urlopen(f"http://127.0.0.1:{port}/email/{encoded}")
        body = resp.read().decode("utf-8")
        assert "doc.pdf" in body
        assert "2,048 bytes" in body
        assert "img.png" in body
        assert "512 bytes" in body
    finally:
        server.shutdown()


def test_move_with_redirect_to(single_db: str) -> None:
    """POST /move with redirect_to redirects to the specified path."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "redirect-me",
                "sender": "x@x.com",
                "subject": "Redirect test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(
            port,
            {
                "message_id": "redirect-me",
                "triage_action": "TO_ARCHIVE",
                "redirect_to": "/email/redirect-me?embed=1",
            },
        )
        assert status == 302, f"Expected 302, got {status}: {body}"

        # Also verify normal redirect still works (no redirect_to)
        status2, _body2 = _post_form(
            port,
            {"message_id": "redirect-me", "triage_action": "TO_ANSWER"},
        )
        assert status2 == 302
    finally:
        server.shutdown()


def test_move_with_empty_redirect_to_falls_back_to_board(single_db: str) -> None:
    """Empty redirect_to should redirect to /board (backward-compatible)."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "fallback-me",
                "sender": "x@x.com",
                "subject": "Fallback test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(
            port,
            {
                "message_id": "fallback-me",
                "triage_action": "TO_ARCHIVE",
                "redirect_to": "",
            },
        )
        assert status == 302, f"Expected 302, got {status}: {body}"
        # Should redirect to /board because redirect_to is empty
        # (the test NoRedirect handler doesn't follow, so we can't
        # check Location directly here; we rely on status 302 and
        # the board counts to verify correctness)
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        board_html = resp.read().decode("utf-8")
        counts = re.findall(
            r'<span class="board-column-count">(\d+)</span>',
            board_html,
        )
        assert counts == ["1"]
    finally:
        server.shutdown()


def test_move_protocol_relative_redirect_falls_back_to_board() -> None:
    """A ``//evil.com`` redirect_to must fall back to /board (open redirect)."""
    resp = _move_and_get_location("//evil.com")
    assert resp.status == 302
    assert resp.headers.get("Location") == "/board"


def test_move_backslash_redirect_falls_back_to_board() -> None:
    """A ``/\\evil.com`` redirect_to must fall back to /board (open redirect)."""
    resp = _move_and_get_location("/\\evil.com")
    assert resp.status == 302
    assert resp.headers.get("Location") == "/board"


def test_move_crlf_redirect_does_not_inject_header() -> None:
    """A CRLF-bearing redirect_to must not inject extra response headers."""
    resp = _move_and_get_location("/board\r\nSet-Cookie: pwned=1")
    assert resp.status == 302
    # The malicious value is neutralized — fall back to /board ...
    assert resp.headers.get("Location") == "/board"
    # ... and no injected header reaches the client.
    assert resp.headers.get("Set-Cookie") is None


def test_move_valid_local_redirect_to_is_preserved() -> None:
    """A valid local redirect_to is used verbatim for the 302 Location."""
    resp = _move_and_get_location("/email/evil-me?embed=1")
    assert resp.status == 302
    assert resp.headers.get("Location") == "/email/evil-me?embed=1"
