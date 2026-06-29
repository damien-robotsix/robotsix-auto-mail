"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

import pytest

if TYPE_CHECKING:
    pass

from tests.server.conftest import (
    _populate_db,
    _start_test_server,
)


def test_make_board_handler_binds_boardhandler_with_db_path() -> None:
    """make_board_handler yields a partial binding BoardHandler + db_path.

    Proves BoardHandler is module-level and testable without the factory.
    """
    from robotsix_auto_mail.server import BoardHandler, make_board_handler

    handler = make_board_handler(":memory:")
    assert handler.func is BoardHandler
    assert handler.keywords == {
        "db_path": ":memory:",
        "mail_config": None,
        "component_responder": None,
    }


def test_handler_root_redirects() -> None:
    from urllib.request import (
        HTTPRedirectHandler,
        build_opener,
    )

    server, port = _start_test_server(":memory:")
    try:

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(
                self,
                req: Request,
                fp: object,
                code: int,
                msg: object,
                hdrs: object,
                newurl: str,
            ) -> None:
                return None  # don't follow

            def http_error_301(
                self,
                req: Request,
                fp: object,
                code: int,
                msg: object,
                hdrs: object,
            ) -> object:
                return fp

        opener = build_opener(NoRedirect())
        resp = opener.open(f"http://127.0.0.1:{port}/")
        assert resp.status == 301
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()


def test_handler_board_returns_200_and_html() -> None:
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "text/html" in content_type
        body = resp.read().decode("utf-8")
        assert "<!DOCTYPE html>" in body
    finally:
        server.shutdown()


def test_board_content_endpoint_returns_json(single_db: str) -> None:
    """GET /board-content returns 200 with application/json."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "bc1",
                "sender": "a@b.com",
                "subject": "Test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "Body",
                "status": "to_read",
            },
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board-content")
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "application/json" in content_type
        body = resp.read().decode("utf-8")
        import json as _json

        payload = _json.loads(body)
        assert isinstance(payload, dict)
        assert "columns_html" in payload
        assert 'class="board-column"' in payload["columns_html"]
    finally:
        server.shutdown()


def test_board_content_endpoint_empty_db_returns_json(single_db: str) -> None:
    """GET /board-content with empty DB returns empty-board placeholder."""
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board-content")
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        import json as _json

        payload = _json.loads(body)
        columns_html = payload["columns_html"]
        assert 'class="empty-board"' in columns_html
        assert "No mail yet." in columns_html
        assert 'class="board-column"' not in columns_html
    finally:
        server.shutdown()


def test_board_content_db_unavailable_returns_503() -> None:
    """GET /board-content with bad DB path returns 503 JSON error."""
    import urllib.error

    server, port = _start_test_server("/dev/null/nonexistent.db")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/board-content")
        assert exc_info.value.code == 503
        body = exc_info.value.read().decode("utf-8")
        payload = json.loads(body)
        assert "error" in payload
        assert "Database unavailable" in payload["error"]
    finally:
        server.shutdown()


def test_handler_nonexistent_returns_404() -> None:
    import urllib.error

    server, port = _start_test_server(":memory:")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/nonexistent")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()


def test_handler_missing_db_returns_503() -> None:
    import urllib.error

    # Point to a path inside /dev/null so init_db raises an error.
    server, port = _start_test_server("/dev/null/nonexistent.db")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/board")
        assert exc_info.value.code == 503
        body = exc_info.value.read().decode("utf-8")
        assert "Database unavailable" in body
    finally:
        server.shutdown()


def test_handler_board_with_data(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "m10",
                "sender": "inbox@test.com",
                "subject": "Inbox Msg",
                "date": "2025-05-01T10:00:00",
                "body_plain": "Hello",
                "status": "to_read",
            },
            {
                "message_id": "m11",
                "sender": "triaging@test.com",
                "subject": "Triaging Msg",
                "date": "2025-05-02T10:00:00",
                "body_plain": "Hi",
                "status": "needs_reply",
            },
            {
                "message_id": "m12",
                "sender": "archive1@test.com",
                "subject": "Archive1",
                "date": "2025-05-03T10:00:00",
                "body_plain": "Yo",
                "status": "no_action",
            },
            {
                "message_id": "m13",
                "sender": "archive2@test.com",
                "subject": "Archive2",
                "date": "2025-05-04T10:00:00",
                "body_plain": "Hey",
                "status": "no_action",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")

        assert "inbox@test.com" in body
        assert "triaging@test.com" in body
        assert "archive1@test.com" in body
        assert "archive2@test.com" in body

        # All records are untriaged (no triage_decisions rows) →
        # they all land in the INBOX column — the only non-empty column.
        counts = re.findall(r'<span class="board-column-count">(\d+)</span>', body)
        assert counts == ["4"]
    finally:
        server.shutdown()


def test_handler_xss_prevention(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "xss1",
                "sender": "<script>alert(1)</script>",
                "subject": "<img onerror=alert(2)>",
                "date": "2025-01-01T00:00:00",
                "body_plain": "<b>evil</b>",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")

        # All angle brackets in user data must be escaped
        assert "&lt;script&gt;" in body
        assert "&lt;img onerror" in body
        assert "&lt;b&gt;evil&lt;/b&gt;" in body
    finally:
        server.shutdown()
