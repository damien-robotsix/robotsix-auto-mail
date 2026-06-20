"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import TYPE_CHECKING
from unittest import mock
from urllib.request import Request, urlopen

import pytest

if TYPE_CHECKING:
    pass

from tests.server.conftest import (
    _account_config,
    _get,
    _move_and_get_location,
    _populate_db,
    _post_config_sync,
    _post_form,
    _seed_triage_decision,
    _start_test_server,
    _start_test_server_with_accounts,
    _start_test_server_with_mail_config,
    _triage_action,
)

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import init_db


def test_make_board_handler_binds_boardhandler_with_db_path() -> None:
    """make_board_handler yields a partial binding BoardHandler + db_path.

    Proves BoardHandler is module-level and testable without the factory.
    """
    from robotsix_auto_mail.server import BoardHandler, make_board_handler

    handler = make_board_handler(":memory:")
    assert handler.func is BoardHandler
    assert handler.keywords == {"db_path": ":memory:", "mail_config": None}


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


# ---------------------------------------------------------------------------
# POST /move tests
# ---------------------------------------------------------------------------


def test_move_success_redirects_302(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "move-me",
                "sender": "x@x.com",
                "subject": "Move test",
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
            {"message_id": "move-me", "triage_action": "TO_ARCHIVE"},
        )
        assert status == 302, f"Expected 302, got {status}: {body}"

        # Verify the card actually moved by checking /board.
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        board_html = resp.read().decode("utf-8")
        # Should be in To archive column — the only non-empty one.
        counts = re.findall(
            r'<span class="board-column-count">(\d+)</span>',
            board_html,
        )
        assert counts == ["1"], f"Unexpected counts: {counts}"
    finally:
        server.shutdown()


def test_move_to_triaging(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "m-triaging",
                "sender": "t@t.com",
                "subject": "Triaging",
                "date": "2025-02-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, _ = _post_form(
            port, {"message_id": "m-triaging", "triage_action": "TO_ANSWER"}
        )
        assert status == 302

        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")
        counts = re.findall(r'<span class="board-column-count">(\d+)</span>', body)
        assert counts == ["1"]
    finally:
        server.shutdown()


def test_move_to_archive(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "m-archive",
                "sender": "a@a.com",
                "subject": "Archive",
                "date": "2025-03-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, _ = _post_form(
            port, {"message_id": "m-archive", "triage_action": "TO_ARCHIVE"}
        )
        assert status == 302

        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")
        counts = re.findall(r'<span class="board-column-count">(\d+)</span>', body)
        assert counts == ["1"]
    finally:
        server.shutdown()


def test_move_invalid_status_returns_400(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "bad-status",
                "sender": "x@x.com",
                "subject": "Bad",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(
            port, {"message_id": "bad-status", "triage_action": "bogus"}
        )
        assert status == 400
        assert "Invalid triage action: 'bogus'" in body
    finally:
        server.shutdown()


def test_move_missing_message_id_returns_400() -> None:
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(port, {"triage_action": "TO_ARCHIVE"})
        assert status == 400
        assert "Missing message_id" in body
    finally:
        server.shutdown()


def test_move_missing_status_returns_400(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "missing-status",
                "sender": "x@x.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "Hello",
                "status": "to_read",
            },
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        status, body = _post_form(port, {"message_id": "missing-status"})
        assert status == 400
        assert "Missing triage_action" in body
    finally:
        server.shutdown()


def test_move_empty_message_id_returns_400() -> None:
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(
            port,
            {"message_id": "  ", "triage_action": "TO_ARCHIVE"},
        )
        assert status == 400
        assert "Missing message_id" in body
    finally:
        server.shutdown()


def test_move_unknown_message_id_returns_404() -> None:
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(
            port, {"message_id": "does-not-exist", "triage_action": "TO_ARCHIVE"}
        )
        assert status == 404
        assert body == "Not found"
    finally:
        server.shutdown()


def test_move_to_archive_triggers_llm(single_db: str) -> None:
    """Moving to TO_ARCHIVE triggers the LLM provider."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "llm-trigger",
                "sender": "dev@python.org",
                "subject": "PEP discussion",
                "date": "2025-06-01T12:00:00",
                "body_plain": "Let's talk about the new PEP.",
                "status": "to_read",
            },
        ],
    )

    mail_config = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user",
        password="pass",
        llm_api_key="sk-test",
    )

    with mock.patch("robotsix_llmio.core.get_provider") as mock_provider_cls:
        server, port = _start_test_server_with_mail_config(single_db, mail_config)
        try:
            status, body = _post_form(
                port,
                {"message_id": "llm-trigger", "triage_action": "TO_ARCHIVE"},
            )
            assert status == 302, f"Expected 302, got {status}: {body}"
            # LLM provider should have been instantiated
            mock_provider_cls.assert_called_once()
        finally:
            server.shutdown()


def test_move_to_archive_llm_failure_still_redirects(single_db: str) -> None:
    """LLM call fails → POST still returns 302."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "llm-fail",
                "sender": "x@x.com",
                "subject": "Test",
                "date": "2025-06-01T12:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    mail_config = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user",
        password="pass",
        llm_api_key="sk-test",
    )

    mock_provider = mock.MagicMock()
    mock_provider.build_agent.side_effect = RuntimeError("LLM crashed")

    with mock.patch(
        "robotsix_llmio.core.get_provider",
        return_value=mock_provider,
    ):
        server, port = _start_test_server_with_mail_config(single_db, mail_config)
        try:
            status, body = _post_form(
                port,
                {"message_id": "llm-fail", "triage_action": "TO_ARCHIVE"},
            )
            assert status == 302, f"Expected 302, got {status}: {body}"

            # Should be in To archive column — the only non-empty one.
            resp = urlopen(f"http://127.0.0.1:{port}/board")
            board_html = resp.read().decode("utf-8")
            counts = re.findall(
                r'<span class="board-column-count">(\d+)</span>',
                board_html,
            )
            assert counts == ["1"], f"Unexpected counts: {counts}"
        finally:
            server.shutdown()


def test_move_to_other_column_skips_llm(single_db: str) -> None:
    """Moving to TO_ANSWER does NOT trigger the LLM."""

    _populate_db(
        single_db,
        [
            {
                "message_id": "skip-llm",
                "sender": "x@x.com",
                "subject": "Question",
                "date": "2025-06-01T12:00:00",
                "body_plain": "Can you help?",
                "status": "to_read",
            },
        ],
    )

    mail_config = MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user",
        password="pass",
        llm_api_key="sk-test",
    )

    with mock.patch("robotsix_llmio.core.get_provider") as mock_provider_cls:
        server, port = _start_test_server_with_mail_config(single_db, mail_config)
        try:
            status, body = _post_form(
                port,
                {"message_id": "skip-llm", "triage_action": "TO_ANSWER"},
            )
            assert status == 302, f"Expected 302, got {status}: {body}"
            # LLM provider should NOT have been instantiated
            mock_provider_cls.assert_not_called()
        finally:
            server.shutdown()


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


# ---------------------------------------------------------------------------
# GET /email/{message_id}?embed=1 handler integration tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# GET /email/{message_id}?embed=1 handler integration tests
# ---------------------------------------------------------------------------


def test_handler_email_detail_embed_returns_fragment(single_db: str) -> None:
    """GET /email/{id}?embed=1 returns HTML fragment without full-page chrome."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "embed-handler@test.com",
                "sender": "eh@test.com",
                "subject": "Embed Handler",
                "date": "2025-01-01T00:00:00",
                "body_plain": "embed handler body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/email/embed-handler@test.com?embed=1")
        assert resp.status == 200
        body = resp.read().decode("utf-8")
        # Fragment — no full-page chrome
        assert "<!DOCTYPE html>" not in body
        assert "<html" not in body
        assert "<title>" not in body
        # But has the content
        assert "eh@test.com" in body
        assert "embed handler body" in body
        assert 'class="embed-detail"' in body
        # Move form with redirect_to
        assert 'name="redirect_to"' in body
    finally:
        server.shutdown()


def test_handler_email_detail_embed_unknown_returns_404() -> None:
    """GET /email/unknown?embed=1 returns 404 (same as non-embed)."""
    server, port = _start_test_server(":memory:")
    try:
        import urllib.error

        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/email/does-not-exist?embed=1")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# POST /config-sync tests
# ---------------------------------------------------------------------------


def test_config_sync_success_returns_200_json(single_db: str) -> None:
    import json as _json

    from robotsix_auto_mail.config.config_sync_agent import (
        ConfigSyncResult,
        DriftProposal,
    )

    fake_result = ConfigSyncResult(
        proposals=[
            DriftProposal(
                title="Default mismatch",
                body="The YAML default differs from the dataclass default.",
                affected_field="timeout",
                confidence="high",
            )
        ]
    )

    import urllib.request

    server, port = _start_test_server(single_db)
    try:
        with mock.patch(
            "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
            return_value=fake_result,
        ) as mocked:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/config-sync",
                data=b"",
                method="POST",
            )
            resp = urlopen(req)  # noqa: S310
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("application/json")
            payload = _json.loads(resp.read().decode("utf-8"))

        assert list(payload.keys()) == ["proposals"]
        assert len(payload["proposals"]) == 1
        proposal = payload["proposals"][0]
        assert proposal["title"] == "Default mismatch"
        assert proposal["affected_field"] == "timeout"
        assert proposal["confidence"] == "high"
        assert "body" in proposal

        # Verify the agent was invoked with a live DB connection so the
        # dedup ledger wiring is exercised.
        assert mocked.call_count == 1
        assert "conn" in mocked.call_args.kwargs
        assert mocked.call_args.kwargs["conn"] is not None
    finally:
        server.shutdown()


def test_config_sync_error_returns_503_json(single_db: str) -> None:
    import json as _json

    from robotsix_auto_mail.config.config_sync_agent import ConfigSyncError

    server, port = _start_test_server(single_db)
    try:
        with mock.patch(
            "robotsix_auto_mail.config.config_sync_agent.run_config_sync_agent",
            side_effect=ConfigSyncError("No LLM API key found"),
        ):
            status, body = _post_config_sync(port)
        assert status == 503
        payload = _json.loads(body)
        assert "error" in payload
        assert "No LLM API key found" in payload["error"]
    finally:
        server.shutdown()


def test_config_sync_unknown_post_path_returns_404() -> None:
    import urllib.error
    import urllib.request

    server, port = _start_test_server(":memory:")
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/no-such-endpoint",
            data=b"",
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(req)  # noqa: S310
        assert exc_info.value.code == 404
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Triage decision display (read-only badge + detail field)
# ---------------------------------------------------------------------------


def test_move_creates_triage_decision(single_db: str) -> None:
    """POST /move creates a triage_decisions row and does NOT update
    mail_records.status."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "move-triage",
                "sender": "x@x.com",
                "subject": "Move triage test",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )

    server, port = _start_test_server(single_db)
    try:
        status, _body = _post_form(
            port, {"message_id": "move-triage", "triage_action": "TO_ARCHIVE"}
        )
        assert status == 302
    finally:
        server.shutdown()

    # Verify triage_decisions row was created.
    from robotsix_auto_mail.triage import get_triage_decision

    conn = init_db(single_db)
    try:
        decision = get_triage_decision(conn, "move-triage")
        assert decision is not None
        assert decision.action == "TO_ARCHIVE"
        assert decision.source == "user"
        assert decision.reason == "moved to TO_ARCHIVE"
        # mail_records.status was NOT updated.
        cur = conn.execute(
            "SELECT status FROM mail_records WHERE message_id = ?",
            ("move-triage",),
        )
        assert cur.fetchone()[0] == "to_read"
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# POST /run-triage tests
# ---------------------------------------------------------------------------


# ===========================================================================
# Board HTML structure tests (HTTP-level)
# ===========================================================================


def test_handler_board_has_library_css_link() -> None:
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")
        assert 'href="/static/board.css"' in body
        assert '<script src="/static/board.js">' in body
    finally:
        server.shutdown()


def test_handler_board_uses_library_css_classes(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "m1",
                "sender": "a@b.com",
                "subject": "S",
                "date": "2025-01-01T00:00:00",
                "body_plain": "B",
                "status": "to_read",
            }
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")
        # Library CSS class names
        assert 'class="board"' in body
        assert 'class="board-column"' in body
        assert 'class="board-card"' in body
        assert 'class="board-column-header"' in body
        assert 'class="board-column-label"' in body
        assert 'class="board-column-count"' in body
        assert 'class="board-column-cards"' in body
        assert 'class="board-card-title"' in body
        assert 'class="board-card-timestamps"' in body
        assert 'class="board-card-move"' in body
        # Auto-mail custom features
        assert 'data-message-id="m1"' in body
        assert "data-subject" in body
        assert "side-panel" in body
        assert "triage-control" in body
    finally:
        server.shutdown()


def test_handler_board_has_auto_refresh_js() -> None:
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")
        # The refresh logic lives in the external board-auto-mail.js overlay.
        assert "/static/board-auto-mail.js" in body
    finally:
        server.shutdown()


def test_handler_board_refresh_preserves_scroll() -> None:
    server, port = _start_test_server(":memory:")
    try:
        # The scroll-preservation logic lives in board-auto-mail.js.
        # Verify the overlay is served and contains the expected code.
        resp = urlopen(f"http://127.0.0.1:{port}/static/board-auto-mail.js")
        body = resp.read().decode("utf-8")
        # refreshBoard saves the scroll offsets before replacing innerHTML.
        assert "window.pageXOffset" in body
        assert "window.pageYOffset" in body
        assert "prevBoard.scrollLeft" in body
        assert "prevBoard.scrollTop" in body
        # ...and restores them after the successful refresh.
        assert "window.scrollTo(savedX, savedY)" in body
        assert "newBoard.scrollLeft = savedBoardLeft" in body
        assert "newBoard.scrollTop = savedBoardTop" in body
    finally:
        server.shutdown()


def test_handler_board_no_manual_controls() -> None:
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")
        # The redundant manual controls are gone from both the
        # server-rendered HTML and the inline refreshBoard() JS strings.
        assert 'id="refresh-btn"' not in body
        assert "Run triage" not in body
        assert 'action="/run-triage"' not in body
        # The informational auto-refresh poll lives in the overlay now.
        assert "/static/board-auto-mail.js" in body
        # The triage-control wrapper stays as the AJAX re-render target.
        assert 'id="triage-control"' in body
    finally:
        server.shutdown()


def test_handler_board_content_json_keys(single_db: str) -> None:
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board-content")
        body = resp.read().decode("utf-8")
        payload = json.loads(body)
        for key in (
            "columns_html",
            "triage_running",
            "batch_op",
            "unsubscribe_suggestions",
        ):
            assert key in payload
        # Idle board → no batch op in flight.
        assert payload["batch_op"] is None
    finally:
        server.shutdown()


# ===========================================================================
# Static asset tests
# ===========================================================================


# ===========================================================================
# Static asset tests
# ===========================================================================


def test_handler_static_board_js_returns_200() -> None:
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/static/board.js")
        assert resp.status == 200
        ct = resp.headers.get("Content-Type", "")
        assert "text/javascript" in ct
        body = resp.read().decode("utf-8")
        assert len(body) > 100
    finally:
        server.shutdown()


def test_handler_static_board_css_returns_200() -> None:
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/static/board.css")
        assert resp.status == 200
        ct = resp.headers.get("Content-Type", "")
        assert "text/css" in ct
        body = resp.read().decode("utf-8")
        assert len(body) > 100
    finally:
        server.shutdown()


def test_handler_static_automail_board_css_returns_200() -> None:
    """GET /static/automail/board.css serves the app-layer stylesheet."""
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/static/automail/board.css")
        assert resp.status == 200
        ct = resp.headers.get("Content-Type", "")
        assert "text/css" in ct
        body = resp.read().decode("utf-8")
        assert len(body) > 100
        # Drawer/scroll styling has a home in the app stylesheet.
        assert ".side-panel" in body
        assert ".side-panel.open" in body
        assert ".board-wrapper" in body
        # The UI background is a dark shade harmonizing with the board palette.
        assert "background: #121626" in body
        # Buttons get a dark default background so none render white on the
        # dark theme.
        assert "button {" in body
        assert "background: #0f3460" in body
    finally:
        server.shutdown()


def test_handler_board_links_app_css_after_library_css() -> None:
    """GET /board links the app stylesheet AFTER the library one."""
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")
        lib_idx = body.find('href="/static/board.css"')
        app_idx = body.find('href="/static/automail/board.css"')
        assert lib_idx != -1
        assert app_idx != -1
        # The app stylesheet must come after the library one so its
        # rules cascade over the library defaults.
        assert lib_idx < app_idx
    finally:
        server.shutdown()


def test_handler_email_detail_links_app_css(single_db: str) -> None:
    """GET /email/{id} links the app stylesheet after the library one."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "css-detail",
                "sender": "x@y.com",
                "subject": "Detail",
                "date": "2025-01-01T00:00:00",
                "body_plain": "B",
                "status": "to_read",
            }
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/email/css-detail")
        body = resp.read().decode("utf-8")
        lib_idx = body.find('href="/static/board.css"')
        app_idx = body.find('href="/static/automail/board.css"')
        assert lib_idx != -1
        assert app_idx != -1
        assert lib_idx < app_idx
    finally:
        server.shutdown()


def test_handler_email_detail_embed_links_app_css(single_db: str) -> None:
    """GET /email/{id}?embed=1 also links the app stylesheet."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "css-embed",
                "sender": "x@y.com",
                "subject": "Embed",
                "date": "2025-01-01T00:00:00",
                "body_plain": "B",
                "status": "to_read",
            }
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/email/css-embed?embed=1")
        body = resp.read().decode("utf-8")
        assert 'href="/static/automail/board.css"' in body
    finally:
        server.shutdown()


def test_handler_board_refresh_board_accepts_force() -> None:
    """/board defines refreshBoard(force) with a force-guarded early return."""
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/static/board-auto-mail.js")
        body = resp.read().decode("utf-8")
        assert "function refreshBoard(force)" in body
        assert "if (!force && sidePanel" in body
        assert '.classList.contains("open")) return;' in body
        # Auto-refresh behaviour preserved.
        assert "setInterval(refreshBoard, 30000)" in body
    finally:
        server.shutdown()


def test_handler_email_detail_embed_notifies_parent_board(single_db: str) -> None:
    """The embed fragment carries a guarded parent-board refresh script."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "notify-embed",
                "sender": "x@y.com",
                "subject": "Embed",
                "date": "2025-01-01T00:00:00",
                "body_plain": "B",
                "status": "to_read",
            }
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/email/notify-embed?embed=1")
        body = resp.read().decode("utf-8")
        assert "window.parent.refreshBoard(true)" in body
        assert "typeof window.parent.refreshBoard === 'function'" in body
    finally:
        server.shutdown()


def test_handler_email_detail_standalone_has_no_parent_refresh(single_db: str) -> None:
    """The standalone (non-embed) detail page must not notify a parent board."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "standalone-detail",
                "sender": "x@y.com",
                "subject": "Standalone",
                "date": "2025-01-01T00:00:00",
                "body_plain": "B",
                "status": "to_read",
            }
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/email/standalone-detail")
        body = resp.read().decode("utf-8")
        assert "window.parent.refreshBoard" not in body
    finally:
        server.shutdown()


def test_handler_board_inline_handlers_resolve_to_defined_functions(
    single_db: str,
) -> None:
    """Regression guard: every inline onclick/onchange/onsubmit handler on
    /board must invoke a function that is defined somewhere reachable by the
    page — an inline ``<script>`` block or a served script — excluding
    native browser built-ins (e.g. ``confirm``).

    This passes today (only ``openDetail``/``closeDetail`` are custom, both
    defined inline) and fails if a future change emits a handler referencing
    an undefined global.
    """
    # Seed a TO_DELETE card so delete/batch-delete/force-triage handlers are
    # all present in the rendered HTML alongside the drawer handlers.
    _populate_db(
        single_db,
        [
            {
                "message_id": "guard-1",
                "sender": "a@b.com",
                "subject": "Guard",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "to_read",
            },
        ],
    )
    _seed_triage_decision(single_db, "guard-1", action="TO_DELETE")

    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        board_html = resp.read().decode("utf-8")
        # Collect the JS reachable by the page: every inline <script>
        # block (no src) plus every served script the page references.
        inline_scripts = re.findall(r"<script>(.*?)</script>", board_html, re.DOTALL)
        served_srcs = re.findall(r'<script src="([^"]+)"', board_html)
        defined_sources = list(inline_scripts)
        for src in served_srcs:
            served = urlopen(f"http://127.0.0.1:{port}{src}")
            defined_sources.append(served.read().decode("utf-8"))
        defined_js = "\n".join(defined_sources)

        # Top-level function identifiers defined in the reachable JS.
        defined_names = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)", defined_js))

        # Native browser built-ins that need no definition.
        native = {
            "confirm",
            "alert",
            "prompt",
            "fetch",
            "setInterval",
            "clearInterval",
            "setTimeout",
            "clearTimeout",
        }

        # Every inline event-handler reference on the page.
        handler_values = re.findall(
            r'(?:onclick|onchange|onsubmit)="([^"]*)"', board_html
        )
        assert handler_values, "expected at least one inline handler on /board"

        invoked: set[str] = set()
        for value in handler_values:
            for ident in re.findall(r"([A-Za-z_$][\w$]*)\s*\(", value):
                invoked.add(ident)

        # openDetail/closeDetail must be among the invoked identifiers.
        assert {"openDetail", "closeDetail"} & invoked

        unresolved = {
            ident
            for ident in invoked
            if ident not in native and ident not in defined_names
        }
        assert not unresolved, (
            f"inline handlers reference undefined functions: {unresolved}"
        )
    finally:
        server.shutdown()


def test_handler_static_unknown_returns_404() -> None:
    import urllib.error

    server, port = _start_test_server(":memory:")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/static/nonexistent.xyz")
        assert exc_info.value.code == 404
    finally:
        server.shutdown()


# ===========================================================================
# Email detail page tests (new patterns)
# ===========================================================================


# ===========================================================================
# Email detail page tests (new patterns)
# ===========================================================================


def test_handler_email_detail_has_board_css(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "em1",
                "sender": "x@y.com",
                "subject": "Detail",
                "date": "2025-01-01T00:00:00",
                "body_plain": "B",
                "status": "to_read",
            }
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/email/em1")
        body = resp.read().decode("utf-8")
        assert '<link rel="stylesheet" href="/static/board.css">' in body
        assert '<a class="back-link"' in body
        assert '<div class="detail-container">' in body
    finally:
        server.shutdown()


def test_handler_email_detail_embed_no_chrome(single_db: str) -> None:
    _populate_db(
        single_db,
        [
            {
                "message_id": "em2",
                "sender": "x@y.com",
                "subject": "Embed",
                "date": "2025-01-01T00:00:00",
                "body_plain": "B",
                "status": "to_read",
            }
        ],
    )
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/email/em2?embed=1")
        body = resp.read().decode("utf-8")
        assert "<!DOCTYPE html>" not in body
        assert "<html" not in body
        assert 'class="embed-detail"' in body
        # The embed fragment must rely solely on the linked app
        # stylesheet — no inline <style> block (it would duplicate
        # rules already defined in board.css).
        assert "<style>" not in body
        assert 'href="/static/automail/board.css"' in body
    finally:
        server.shutdown()


def test_make_board_handler_with_accounts_adds_keywords(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """With accounts, the partial carries the extra resolution keywords."""
    from robotsix_auto_mail.server import make_board_handler

    db_a, _db_b, accounts = db_accounts_no_triage
    handler = make_board_handler(
        db_a,
        mail_config=accounts.get("A").config,
        accounts=accounts,
        default_account_id="A",
    )
    assert handler.keywords["accounts"] is accounts
    assert handler.keywords["default_account_id"] == "A"


def test_query_string_tolerant_routing(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /board?account=A and POST /move?account=A dispatch (not 404)."""
    from urllib.request import Request

    _db_a, _db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, _body, _hdrs = _get(f"http://127.0.0.1:{port}/board?account=A")
        assert status == 200

        data = b"message_id=msg-a&triage_action=INBOX"
        req = Request(
            f"http://127.0.0.1:{port}/move?account=A",
            data=data,
            method="POST",
        )
        resp = urlopen(req)  # noqa: S310
        try:
            # 301 redirect on success, not 404.
            assert resp.status in (200, 301)
        finally:
            resp.close()
    finally:
        server.shutdown()


def test_get_routing_isolates_accounts(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /board-content?account=<id> serves only that account's records."""
    _db_a, _db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        _s, body_a, _h = _get(f"http://127.0.0.1:{port}/board-content?account=A")
        assert "alice@a.com" in body_a
        assert "bob@b.com" not in body_a

        _s, body_b, _h = _get(f"http://127.0.0.1:{port}/board-content?account=B")
        assert "bob@b.com" in body_b
        assert "alice@a.com" not in body_b
    finally:
        server.shutdown()


def test_get_default_account_no_param(
    db_accounts_no_triage_b: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /board-content with no param and ≥2 accounts defaults to aggregate."""
    _db_a, _db_b, accounts = db_accounts_no_triage_b
    server, port = _start_test_server_with_accounts(accounts, "B")
    try:
        _s, body, _h = _get(f"http://127.0.0.1:{port}/board-content")
        # Aggregate view shows cards from both accounts.
        assert "bob@b.com" in body
        assert "alice@a.com" in body
    finally:
        server.shutdown()


def test_post_move_isolates_accounts(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """POST /move?account=B writes only to B's DB; A's DB is untouched."""
    from urllib.request import Request

    db_a, db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        data = b"message_id=msg-b&triage_action=TO_ANSWER"
        req = Request(
            f"http://127.0.0.1:{port}/move?account=B",
            data=data,
            method="POST",
        )
        resp = urlopen(req)  # noqa: S310
        resp.close()

        assert _triage_action(db_b, "msg-b") == "TO_ANSWER"
        # Account A's DB is untouched (no decision for msg-a).
        assert _triage_action(db_a, "msg-a") is None
    finally:
        server.shutdown()


def test_unknown_explicit_account_is_404(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """Explicit ?account=bogus → 404 on both GET and POST."""
    from urllib.error import HTTPError
    from urllib.request import Request

    _db_a, _db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        with pytest.raises(HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/board?account=bogus").close()
        assert exc_info.value.code == 404

        req = Request(
            f"http://127.0.0.1:{port}/move?account=bogus",
            data=b"message_id=msg-a&triage_action=read",
            method="POST",
        )
        with pytest.raises(HTTPError) as exc_info:
            urlopen(req).close()  # noqa: S310
        assert exc_info.value.code == 404
    finally:
        server.shutdown()


def test_stale_cookie_falls_back_to_default(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """A stale/unknown id from the cookie is ignored — default served, no 404."""
    _db_a, _db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, body, _h = _get(
            f"http://127.0.0.1:{port}/board-content",
            cookie="account=bogus",
        )
        assert status == 200
        assert "alice@a.com" in body
    finally:
        server.shutdown()


def test_cookie_persistence(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /board?account=B sets the cookie; a cookie-only request serves B."""
    _db_a, _db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        _s, _body, headers = _get(f"http://127.0.0.1:{port}/board?account=B")
        assert headers.get("Set-Cookie") == "account=B; Path=/"

        _s, body, _h = _get(
            f"http://127.0.0.1:{port}/board-content",
            cookie="account=B",
        )
        assert "bob@b.com" in body
        assert "alice@a.com" not in body
    finally:
        server.shutdown()


def test_picker_visible_multi_account(
    db_accounts_with_labels_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """A 2-account board defaults to aggregate with 'All mailboxes' selected."""
    _db_a, _db_b, accounts = db_accounts_with_labels_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, body, _h = _get(f"http://127.0.0.1:{port}/board")
        assert status == 200
        assert '<select id="account-picker"' in body
        assert '<option value="__all__"' in body
        assert '<option value="A"' in body
        assert '<option value="B"' in body
        # Default (no query, no cookie, ≥2 accounts) → aggregate.
        assert '<option value="__all__" selected>' in body
        assert '<option value="A" selected>' not in body
        # Non-None label renders escaped as the option text.
        assert "Alice &lt;Work&gt;" in body
    finally:
        server.shutdown()


def test_picker_reflects_selection(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /board?account=B marks the B option selected (not A)."""
    _db_a, _db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, body, _h = _get(f"http://127.0.0.1:{port}/board?account=B")
        assert status == 200
        assert '<option value="B" selected>' in body
        assert '<option value="A" selected>' not in body
        # Aggregate sentinel is present but not selected.
        assert '<option value="__all__">All mailboxes</option>' in body
        assert '<option value="__all__" selected>' not in body
    finally:
        server.shutdown()


def test_picker_onchange_navigates(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """The picker reload handler navigates to ?account=."""
    _db_a, _db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        _s, body, _h = _get(f"http://127.0.0.1:{port}/board")
        assert "window.location.href='/board?account='" in body
    finally:
        server.shutdown()


def test_account_threaded_into_js_urls(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """At ?account=B the detail iframe + content fetch carry account=B."""
    _db_a, _db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        _s, body, _h = _get(f"http://127.0.0.1:{port}/board?account=B")
        # The account query strings are now carried in the #board-config
        # JSON element, consumed by board-auto-mail.js at runtime.
        assert '"account_qs": "&account=B"' in body
        assert '"fetch_qs": "?account=B"' in body
    finally:
        server.shutdown()


def test_detail_panel_shows_selected_account_data(
    db_accounts_no_triage: tuple[str, str, MailAccountsConfig],
) -> None:
    """GET /email/<msg-in-B>?embed=1&account=B returns B's data."""
    _db_a, _db_b, accounts = db_accounts_no_triage
    server, port = _start_test_server_with_accounts(accounts, "A")
    try:
        status, body, _h = _get(
            f"http://127.0.0.1:{port}/email/msg-b?embed=1&account=B"
        )
        assert status == 200
        assert "bob@b.com" in body
    finally:
        server.shutdown()


def test_single_account_container_renders_no_picker(single_db: str) -> None:
    """A 1-element MailAccountsConfig renders no picker and no account param."""
    _populate_db(
        single_db,
        [
            {
                "message_id": "msg-a",
                "sender": "alice@a.com",
                "subject": "From A",
                "date": "2025-01-01T00:00:00",
                "body_plain": "Body A",
                "status": "to_read",
            },
        ],
    )
    accounts = MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="default", config=_account_config(single_db), label=None
            ),
        ),
        default_account_id="default",
    )
    server, port = _start_test_server_with_accounts(accounts, "default")
    try:
        _s, body, _h = _get(f"http://127.0.0.1:{port}/board")
        assert '<select id="account-picker"' not in body
        assert "account=" not in body
    finally:
        server.shutdown()


def test_build_board_html_legacy_no_accounts_kwarg(single_db: str) -> None:
    """Direct _build_board_html(db_path) produces no picker, unchanged URLs."""
    from robotsix_auto_mail.server.views import _build_board_html

    _populate_db(
        single_db,
        [
            {
                "message_id": "msg-a",
                "sender": "alice@a.com",
                "subject": "From A",
                "date": "2025-01-01T00:00:00",
                "body_plain": "Body A",
                "status": "to_read",
            },
        ],
    )
    body = _build_board_html(single_db)
    assert '<select id="account-picker"' not in body
    # The board-auto-mail.js overlay handles URL construction; the
    # #board-config carries the query-string fragments.
    assert '"fetch_qs": ""' in body
    assert '"account_qs": ""' in body
    assert '"data_account_js": false' in body


# ===========================================================================
# GET /healthz tests
# ===========================================================================


def test_healthz_valid_db_returns_200(single_db: str) -> None:
    """GET /healthz with a valid DB returns 200 and {"status": "healthy"}."""
    server, port = _start_test_server(single_db)
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/healthz")
        assert resp.status == 200
        content_type = resp.headers.get("Content-Type", "")
        assert "application/json" in content_type
        body = resp.read().decode("utf-8")
        import json as _json

        payload = _json.loads(body)
        assert payload == {"status": "healthy"}
    finally:
        server.shutdown()


def test_healthz_missing_db_returns_503() -> None:
    """GET /healthz with a missing/corrupt DB returns 503 and error payload."""
    import urllib.error

    server, port = _start_test_server("/dev/null/nonexistent.db")
    try:
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urlopen(f"http://127.0.0.1:{port}/healthz")
        assert exc_info.value.code == 503
        content_type = exc_info.value.headers.get("Content-Type", "")
        assert "application/json" in content_type
        body = exc_info.value.read().decode("utf-8")
        payload = json.loads(body)
        assert payload["status"] == "unhealthy"
        assert "database" in payload["checks"]
        assert payload["checks"]["database"] == "unreachable"
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


def test_healthz_content_type_is_json() -> None:
    """GET /healthz response Content-Type is application/json."""
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/healthz")
        content_type = resp.headers.get("Content-Type", "")
        assert "application/json" in content_type
    finally:
        server.shutdown()
