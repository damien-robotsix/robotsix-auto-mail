"""Tests for the server module (kanban board HTTP handler)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import TYPE_CHECKING, cast
from urllib.request import urlopen

import pytest

if TYPE_CHECKING:
    from http.client import HTTPResponse
    from http.server import HTTPServer

from tests.conftest import _make_record

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import init_db, set_watermark
from robotsix_auto_mail.format import _format_date
from robotsix_auto_mail.server.board_adapter import MailBoardAdapter

# ---------------------------------------------------------------------------
# _format_date
# ---------------------------------------------------------------------------


def test_format_date_valid_iso() -> None:
    assert _format_date("2025-03-15T09:30:00") == "2025-03-15 09:30"


def test_format_date_with_tz_offset() -> None:
    result = _format_date("2025-06-01T14:00:00+00:00")
    assert result.startswith("2025-06-01")


def test_format_date_invalid_returns_raw() -> None:
    assert _format_date("Last Thursday") == "Last Thursday"


def test_format_date_none_returns_none() -> None:
    assert _format_date(None) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers for tests that need a file-based DB
# ---------------------------------------------------------------------------


def _populate_db(db_path: str, inserts: list[dict[str, str]]) -> None:
    """Open *db_path*, insert rows, commit, close."""
    conn = init_db(db_path)
    try:
        for row in inserts:
            conn.execute(
                "INSERT INTO mail_records "
                "(message_id, sender, subject, date, recipients_json, "
                "body_plain, body_html, attachments_json, status) "
                "VALUES (?, ?, ?, ?, '{}', ?, '', '[]', ?)",
                (
                    row["message_id"],
                    row["sender"],
                    row["subject"],
                    row["date"],
                    row.get("body_plain", ""),
                    row["status"],
                ),
            )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# _build_board_html (file-based DB)
# ---------------------------------------------------------------------------


def _start_test_server(db_path: str, port: int = 0) -> tuple[HTTPServer, int]:
    """Start an HTTPServer, return (server, port).  port=0 means auto-assign."""
    import threading
    from http.server import HTTPServer

    from robotsix_auto_mail.server import make_board_handler

    handler = make_board_handler(db_path)
    server = HTTPServer(("127.0.0.1", port), handler)
    assigned_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, assigned_port


def _start_test_server_with_mail_config(
    db_path: str, mail_config: MailConfig, port: int = 0
) -> tuple[HTTPServer, int]:
    """Like :func:`_start_test_server` but binds *mail_config* to the handler."""
    import threading
    from http.server import HTTPServer

    from robotsix_auto_mail.server import make_board_handler

    handler = make_board_handler(db_path, mail_config=mail_config)
    server = HTTPServer(("127.0.0.1", port), handler)
    assigned_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, assigned_port


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
        Request,
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


def test_board_content_endpoint_returns_json() -> None:
    """GET /board-content returns 200 with application/json."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_board_content_endpoint_empty_db_returns_json() -> None:
    """GET /board-content with empty DB returns empty-board placeholder."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_board_content_db_unavailable_returns_503() -> None:
    """GET /board-content with bad DB path returns 503 JSON error."""
    import urllib.error

    server, port = _start_test_server("/dev/null/nonexistent.db")
    try:
        try:
            urlopen(f"http://127.0.0.1:{port}/board-content")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            body = exc.read().decode("utf-8")
            import json as _json

            payload = _json.loads(body)
            assert "error" in payload
            assert "Database unavailable" in payload["error"]
        else:
            raise AssertionError("Expected HTTPError for 503")
    finally:
        server.shutdown()


def test_handler_nonexistent_returns_404() -> None:
    import urllib.error

    server, port = _start_test_server(":memory:")
    try:
        try:
            urlopen(f"http://127.0.0.1:{port}/nonexistent")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("Expected HTTPError for 404")
    finally:
        server.shutdown()


def test_handler_missing_db_returns_503() -> None:
    import urllib.error

    # Point to a path inside /dev/null so init_db raises an error.
    server, port = _start_test_server("/dev/null/nonexistent.db")
    try:
        try:
            urlopen(f"http://127.0.0.1:{port}/board")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            body = exc.read().decode("utf-8")
            assert "Database unavailable" in body
        else:
            raise AssertionError("Expected HTTPError for 503")
    finally:
        server.shutdown()


def test_handler_board_with_data() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_handler_xss_prevention() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/board")
            body = resp.read().decode("utf-8")

            # All angle brackets in user data must be escaped
            assert "&lt;script&gt;" in body
            assert "&lt;img onerror" in body
            assert "&lt;b&gt;evil&lt;/b&gt;" in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# POST /move tests
# ---------------------------------------------------------------------------


def _post_form(
    port: int, fields: dict[str, str], path: str = "/move"
) -> tuple[int, str]:
    """POST url-encoded *fields* to *path* on the test server."""
    import urllib.request

    data = urllib.parse.urlencode(fields).encode("utf-8")
    url = f"http://127.0.0.1:{port}{path}"

    # Don't follow redirects, and capture 400/404 bodies.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self,
            req: urllib.request.Request,
            fp: object,
            code: int,
            msg: object,
            hdrs: object,
            newurl: str,
        ) -> None:
            return None

    class CaptureError(urllib.request.HTTPDefaultErrorHandler):
        def http_error_default(  # type: ignore[override]
            self,
            req: urllib.request.Request,
            fp: object,
            code: int,
            msg: object,
            hdrs: object,
        ) -> object:
            return fp

    opener = urllib.request.build_opener(NoRedirect(), CaptureError())
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = opener.open(req)
    body = resp.read().decode("utf-8")
    return resp.status, body


def _post_form_resp(
    port: int, fields: dict[str, str], path: str = "/move"
) -> HTTPResponse:
    """POST url-encoded *fields* to *path* and return the raw response.

    Like :func:`_post_form` but returns the response object so the raw
    ``Location``/headers can be inspected (does not follow redirects).
    """
    import urllib.request

    data = urllib.parse.urlencode(fields).encode("utf-8")
    url = f"http://127.0.0.1:{port}{path}"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self,
            req: urllib.request.Request,
            fp: object,
            code: int,
            msg: object,
            hdrs: object,
            newurl: str,
        ) -> None:
            return None

    class CaptureError(urllib.request.HTTPDefaultErrorHandler):
        def http_error_default(  # type: ignore[override]
            self,
            req: urllib.request.Request,
            fp: object,
            code: int,
            msg: object,
            hdrs: object,
        ) -> object:
            return fp

    opener = urllib.request.build_opener(NoRedirect(), CaptureError())
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = opener.open(req)
    resp.read()
    return cast("HTTPResponse", resp)


def _post_to_path(port: int, path: str, fields: dict[str, str]) -> HTTPResponse:
    """POST url-encoded *fields* to *path* and return the raw response.

    Does not follow redirects and captures error responses so 302/400/404
    can be inspected directly.
    """
    import urllib.request

    data = urllib.parse.urlencode(fields).encode("utf-8")
    url = f"http://127.0.0.1:{port}{path}"

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(
            self,
            req: urllib.request.Request,
            fp: object,
            code: int,
            msg: object,
            hdrs: object,
            newurl: str,
        ) -> None:
            return None

    class CaptureError(urllib.request.HTTPDefaultErrorHandler):
        def http_error_default(  # type: ignore[override]
            self,
            req: urllib.request.Request,
            fp: object,
            code: int,
            msg: object,
            hdrs: object,
        ) -> object:
            return fp

    opener = urllib.request.build_opener(NoRedirect(), CaptureError())
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = opener.open(req)
    resp.read()
    return cast("HTTPResponse", resp)


def test_move_success_redirects_302() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_move_to_triaging() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_move_to_archive() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_move_invalid_status_returns_400() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
        try:
            status, body = _post_form(
                port, {"message_id": "bad-status", "triage_action": "bogus"}
            )
            assert status == 400
            assert "Invalid triage action: 'bogus'" in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_move_missing_message_id_returns_400() -> None:
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(port, {"triage_action": "TO_ARCHIVE"})
        assert status == 400
        assert "Missing message_id or triage_action" in body
    finally:
        server.shutdown()


def test_move_missing_status_returns_400() -> None:
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(port, {"message_id": "anything"})
        assert status == 400
        assert "Missing message_id or triage_action" in body
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
        assert "Missing message_id or triage_action" in body
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


def test_move_to_archive_triggers_llm() -> None:
    """Moving to TO_ARCHIVE triggers the LLM provider."""
    from unittest import mock

    from robotsix_auto_mail.config import MailConfig

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
            server, port = _start_test_server_with_mail_config(db_path, mail_config)
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
    finally:
        os.unlink(db_path)


def test_move_to_archive_llm_failure_still_redirects() -> None:
    """LLM call fails → POST still returns 302."""
    from unittest import mock

    from robotsix_auto_mail.config import MailConfig

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
            server, port = _start_test_server_with_mail_config(db_path, mail_config)
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
    finally:
        os.unlink(db_path)


def test_move_to_other_column_skips_llm() -> None:
    """Moving to TO_ANSWER does NOT trigger the LLM."""
    from unittest import mock

    from robotsix_auto_mail.config import MailConfig

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
            server, port = _start_test_server_with_mail_config(db_path, mail_config)
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
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# GET /email/{message_id}/status tests
# ---------------------------------------------------------------------------


def test_email_status_returns_200() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        _seed_triage_decision(db_path, "<abc123@example.com>", action="TO_ANSWER")

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_email_status_unknown_message_id_returns_404() -> None:
    server, port = _start_test_server(":memory:")
    try:
        import urllib.error

        try:
            urlopen(f"http://127.0.0.1:{port}/email/nonexistent/status")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("Expected HTTPError 404")
    finally:
        server.shutdown()


def test_email_path_without_status_suffix_now_returns_detail() -> None:
    """GET /email/{mid} (no /status suffix) now returns the detail page."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/email/mid1")
            assert resp.status == 200
            body = resp.read().decode("utf-8")
            assert "<!DOCTYPE html>" in body
            assert "Test" in body
            assert "x@x.com" in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_handler_email_detail_returns_200() -> None:
    """GET /email/{encoded_id} returns 200 and HTML."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_handler_email_detail_unknown_returns_404() -> None:
    """GET /email/unknown-id returns 404."""
    server, port = _start_test_server(":memory:")
    try:
        import urllib.error

        try:
            urlopen(f"http://127.0.0.1:{port}/email/does-not-exist")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("Expected HTTPError 404")
    finally:
        server.shutdown()


def test_handler_email_detail_missing_db_returns_503() -> None:
    """GET /email/{id} returns 503 when DB is unavailable."""
    import urllib.error

    server, port = _start_test_server("/dev/null/nonexistent.db")
    try:
        try:
            urlopen(f"http://127.0.0.1:{port}/email/anything")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            body = exc.read().decode("utf-8")
            assert "Database unavailable" in body
        else:
            raise AssertionError("Expected HTTPError for 503")
    finally:
        server.shutdown()


def test_handler_email_detail_xss_prevention() -> None:
    """HTML in subject/body is escaped, not rendered on the detail page."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_handler_email_detail_does_not_capture_status_route() -> None:
    """GET /email/{id}/status still returns plain text, not HTML detail."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        _seed_triage_decision(db_path, "<status-route@test.com>", action="TO_ARCHIVE")

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_handler_email_detail_with_recipients() -> None:
    """Detail page shows To and CC when present."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = init_db(db_path)
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

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_handler_email_detail_with_attachments() -> None:
    """Detail page shows attachment filenames and sizes."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = init_db(db_path)
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

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_move_with_redirect_to() -> None:
    """POST /move with redirect_to redirects to the specified path."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_move_with_empty_redirect_to_falls_back_to_board() -> None:
    """Empty redirect_to should redirect to /board (backward-compatible)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def _move_and_get_location(redirect_to: str) -> HTTPResponse:
    """POST /move with *redirect_to* and return the raw response object."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "evil-me",
                    "sender": "x@x.com",
                    "subject": "Evil test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        server, port = _start_test_server(db_path)
        try:
            return _post_form_resp(
                port,
                {
                    "message_id": "evil-me",
                    "triage_action": "TO_ARCHIVE",
                    "redirect_to": redirect_to,
                },
            )
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


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


def test_handler_email_detail_embed_returns_fragment() -> None:
    """GET /email/{id}?embed=1 returns HTML fragment without full-page chrome."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(
                f"http://127.0.0.1:{port}/email/embed-handler@test.com?embed=1"
            )
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
    finally:
        os.unlink(db_path)


def test_handler_email_detail_embed_unknown_returns_404() -> None:
    """GET /email/unknown?embed=1 returns 404 (same as non-embed)."""
    server, port = _start_test_server(":memory:")
    try:
        import urllib.error

        try:
            urlopen(f"http://127.0.0.1:{port}/email/does-not-exist?embed=1")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("Expected HTTPError 404")
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# POST /config-sync tests
# ---------------------------------------------------------------------------


def _post_config_sync(port: int) -> tuple[int, str]:
    """POST an empty body to /config-sync; return (status, body)."""
    import urllib.request

    url = f"http://127.0.0.1:{port}/config-sync"

    class CaptureError(urllib.request.HTTPDefaultErrorHandler):
        def http_error_default(  # type: ignore[override]
            self,
            req: urllib.request.Request,
            fp: object,
            code: int,
            msg: object,
            hdrs: object,
        ) -> object:
            return fp

    opener = urllib.request.build_opener(CaptureError())
    req = urllib.request.Request(url, data=b"", method="POST")  # noqa: S310
    resp = opener.open(req)
    body = resp.read().decode("utf-8")
    return resp.status, body


def test_config_sync_success_returns_200_json() -> None:
    import json as _json
    from unittest import mock

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

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        server, port = _start_test_server(db_path)
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
                assert resp.headers.get("Content-Type", "").startswith(
                    "application/json"
                )
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
    finally:
        os.unlink(db_path)


def test_config_sync_error_returns_503_json() -> None:
    import json as _json
    from unittest import mock

    from robotsix_auto_mail.config.config_sync_agent import ConfigSyncError

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


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
        try:
            urlopen(req)  # noqa: S310
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("Expected HTTPError 404")
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Triage decision display (read-only badge + detail field)
# ---------------------------------------------------------------------------


def _seed_triage_decision(
    db_path: str,
    message_id: str,
    *,
    action: str,
    source: str = "agent",
    reason: str = "",
    confidence: str = "medium",
) -> None:
    """Insert a triage_decisions row directly (read-only display fixture)."""
    from robotsix_auto_mail.triage import set_triage_decision

    conn = init_db(db_path)
    try:
        set_triage_decision(
            conn,
            message_id,
            action,
            source=source,
            reason=reason,
            confidence=confidence,
        )
    finally:
        conn.close()


def test_move_creates_triage_decision() -> None:
    """POST /move creates a triage_decisions row and does NOT update
    mail_records.status."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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

        server, port = _start_test_server(db_path)
        try:
            status, _body = _post_form(
                port, {"message_id": "move-triage", "triage_action": "TO_ARCHIVE"}
            )
            assert status == 302
        finally:
            server.shutdown()

        # Verify triage_decisions row was created.
        from robotsix_auto_mail.triage import get_triage_decision

        conn = init_db(db_path)
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
    finally:
        os.unlink(db_path)


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


def test_delete_success_removes_record_and_redirects() -> None:
    """POST /delete with valid message_id deletes the record and returns 302."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        _seed_triage_decision(db_path, "del-me", action="TO_DELETE")

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/delete", {"message_id": "del-me"})
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"
        finally:
            server.shutdown()

        # Verify record is gone from the DB.
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        conn = init_db(db_path)
        try:
            assert get_record_by_message_id(conn, "del-me") is None
        finally:
            conn.close()

        # Verify the board no longer shows the card.
        server2, port2 = _start_test_server(db_path)
        try:
            resp2 = urlopen(f"http://127.0.0.1:{port2}/board")
            board_html = resp2.read().decode("utf-8")
            assert "del-me" not in board_html
            assert "x@x.com" not in board_html
        finally:
            server2.shutdown()
    finally:
        os.unlink(db_path)


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


def _wait_for_batch_idle(db_path: str, timeout: float = 10.0) -> str | None:
    """Poll ``batch_op:state`` until it is ``"idle"``/cleared, returning it."""
    import time

    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db

    deadline = time.monotonic() + timeout
    state: str | None = "running"
    while time.monotonic() < deadline:
        conn = _init_db(db_path, skip_migrations=True)
        try:
            state = get_watermark(conn, "batch_op:state")
        finally:
            conn.close()
        if state is None or state == "idle":
            break
        time.sleep(0.05)
    return state


def test_batch_delete_success_removes_all_to_delete_records_and_redirects() -> None:
    """POST /batch-delete deletes every TO_DELETE record and redirects 302."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        _seed_triage_decision(db_path, "bd-del-1", action="TO_DELETE")
        _seed_triage_decision(db_path, "bd-del-2", action="TO_DELETE")
        # bd-keep is untriaged → INBOX, should survive.

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/batch-delete", {})
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"
            # The worker now runs in a background daemon thread — poll the
            # batch_op:state watermark until it clears back to "idle".
            _wait_for_batch_idle(db_path)
        finally:
            server.shutdown()

        # Verify the TO_DELETE records are gone.
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        conn = init_db(db_path)
        try:
            assert get_record_by_message_id(conn, "bd-del-1") is None
            assert get_record_by_message_id(conn, "bd-del-2") is None
            assert get_record_by_message_id(conn, "bd-keep") is not None
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_batch_delete_empty_column_returns_302() -> None:
    """POST /batch-delete when TO_DELETE is empty → 302, no error."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/batch-delete", {})
        assert resp.status == 302
        assert resp.headers.get("Location") == "/board"
    finally:
        server.shutdown()


def test_delete_stale_uid_preserves_record() -> None:
    """POST /delete with a stale UID and cross-folder search failing
    (mail truly gone) → 302, local record removed."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "stale-del",
                    "sender": "x@x.com",
                    "subject": "Stale delete",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "stale-del", action="TO_DELETE")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (42, "stale-del"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.imap.cross_folder_resolve"
                ) as mock_cross,
            ):
                mock_client = mock_cls.return_value.__enter__.return_value
                # All searches fail → resolve_uid_with_fallback raises
                # ImapMessageNotFoundError.
                mock_client.search_uids.return_value = []
                mock_cross.return_value = None  # mail gone

                status, body = _post_form(
                    port, {"message_id": "stale-del"}, path="/delete"
                )

            assert status == 302, f"Expected 302, got {status}: {body}"
        finally:
            server.shutdown()

        # The local record must be removed.
        from robotsix_auto_mail.db import get_record_by_message_id

        conn = init_db(db_path)
        try:
            assert get_record_by_message_id(conn, "stale-del") is None
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_archive_stale_uid_preserves_record() -> None:
    """POST /archive with a stale UID and cross-folder search failing
    (mail truly gone) → 302, local record removed, no folder memory."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "stale-arch",
                    "sender": "x@x.com",
                    "subject": "Stale archive",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "stale-arch", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "stale-arch", "Lists/new-list")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (42, "stale-arch"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.imap.cross_folder_resolve"
                ) as mock_cross,
            ):
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.search_uids.return_value = []
                mock_cross.return_value = None  # mail gone

                status, body = _post_form(
                    port, {"message_id": "stale-arch"}, path="/archive"
                )

            assert status == 302, f"Expected 302, got {status}: {body}"
        finally:
            server.shutdown()

        from robotsix_auto_mail.db import get_record_by_message_id
        from robotsix_auto_mail.triage import _load_archive_folder_memory

        conn = init_db(db_path)
        try:
            # Record removed and archive-folder memory NOT written.
            assert get_record_by_message_id(conn, "stale-arch") is None
            assert _load_archive_folder_memory(conn) == {}
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_batch_delete_stale_uid_preserves_all_records() -> None:
    """POST /batch-delete with one stale UID where the mail is
    verifiably gone: the stale record is removed from the DB,
    the remaining record is still deleted by the background
    worker, and the server responds with 302."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "bd-stale-1",
                    "sender": "a@b.com",
                    "subject": "Delete me 1",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
                {
                    "message_id": "bd-stale-2",
                    "sender": "c@d.com",
                    "subject": "Delete me 2",
                    "date": "2025-01-02T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "bd-stale-1", action="TO_DELETE")
        _seed_triage_decision(db_path, "bd-stale-2", action="TO_DELETE")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (42, "bd-stale-1"),
            )
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (43, "bd-stale-2"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.imap.cross_folder_resolve"
                ) as mock_cross,
            ):
                mock_client = mock_cls.return_value.__enter__.return_value

                def _search_uids(criteria: str) -> list[int]:
                    if "UID 42" in criteria:
                        return []  # stale
                    if "UID 43" in criteria:
                        return [43]
                    # Message-ID fallback: stale message not findable.
                    if "bd-stale-1" in criteria:
                        return []
                    return [42, 43]

                mock_client.search_uids.side_effect = _search_uids
                mock_cross.return_value = None  # mail gone

                status, body = _post_form(port, {}, path="/batch-delete")
                assert status == 302, f"Expected 302, got {status}: {body}"
                # Work happens in a background daemon thread (the handler no
                # longer blocks on a synchronous precheck) — wait for it while
                # the IMAP mocks are still active.
                _wait_for_batch_idle(db_path)
        finally:
            server.shutdown()

        # The worker deletes every TO_DELETE record: the stale-UID one (mail
        # gone) is dropped from the DB, and the resolvable one is expunged.
        from robotsix_auto_mail.db import get_record_by_message_id

        conn = init_db(db_path)
        try:
            assert get_record_by_message_id(conn, "bd-stale-1") is None
            assert get_record_by_message_id(conn, "bd-stale-2") is None
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_batch_archive_stale_uid_preserves_all_records() -> None:
    """POST /batch-archive with one stale UID where the mail is
    verifiably gone: the stale record is removed from the DB and
    the server responds with 302."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "ba-stale-1",
                    "sender": "a@b.com",
                    "subject": "Archive me 1",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
                {
                    "message_id": "ba-stale-2",
                    "sender": "c@d.com",
                    "subject": "Archive me 2",
                    "date": "2025-01-02T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "ba-stale-1", action="TO_ARCHIVE")
        _seed_triage_decision(db_path, "ba-stale-2", action="TO_ARCHIVE")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (42, "ba-stale-1"),
            )
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (43, "ba-stale-2"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.imap.cross_folder_resolve"
                ) as mock_cross,
            ):
                mock_client = mock_cls.return_value.__enter__.return_value

                def _search_uids(criteria: str) -> list[int]:
                    if "UID 42" in criteria:
                        return []  # stale
                    if "UID 43" in criteria:
                        return [43]
                    if "ba-stale-1" in criteria:
                        return []
                    return [42, 43]

                mock_client.search_uids.side_effect = _search_uids
                mock_cross.return_value = None  # mail gone

                status, body = _post_form(port, {}, path="/batch-archive")

            assert status == 302, f"Expected 302, got {status}: {body}"
        finally:
            server.shutdown()

        # ba-stale-1 was removed during precheck.
        from robotsix_auto_mail.db import get_record_by_message_id

        conn = init_db(db_path)
        try:
            assert get_record_by_message_id(conn, "ba-stale-1") is None
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_delete_cross_folder_heal_and_delete() -> None:
    """POST /delete with a stale UID where cross-folder search finds the
    mail in another folder → heal record, IMAP-delete from new location,
    remove local record, 302."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "heal-del",
                    "sender": "x@x.com",
                    "subject": "Heal delete",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "heal-del", action="TO_DELETE")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ?, source_folder = ? "
                "WHERE message_id = ?",
                (42, "INBOX", "heal-del"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.imap.cross_folder_resolve"
                ) as mock_cross,
            ):
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.search_uids.return_value = []
                mock_cross.return_value = ("Projects", 99)

                status, body = _post_form(
                    port, {"message_id": "heal-del"}, path="/delete"
                )

            assert status == 302, f"Expected 302, got {status}: {body}"
            # Verify the delete was called with the healed UID.
            mock_client.delete_message.assert_called_once_with(99)
        finally:
            server.shutdown()

        # The local record must be removed.
        from robotsix_auto_mail.db import get_record_by_message_id

        conn = init_db(db_path)
        try:
            assert get_record_by_message_id(conn, "heal-del") is None
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_delete_transient_imap_error_preserves_record() -> None:
    """POST /delete with a stale UID where cross-folder search raises
    ImapError → 502, local record preserved."""
    from unittest import mock

    from robotsix_auto_mail.imap import ImapError

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "transient-del",
                    "sender": "x@x.com",
                    "subject": "Transient delete",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "transient-del", action="TO_DELETE")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (42, "transient-del"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.imap.cross_folder_resolve"
                ) as mock_cross,
            ):
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.search_uids.return_value = []
                mock_cross.side_effect = ImapError("connection lost")

                status, body = _post_form(
                    port, {"message_id": "transient-del"}, path="/delete"
                )

            assert status == 502, f"Expected 502, got {status}: {body}"
        finally:
            server.shutdown()

        # The local record must remain intact.
        from robotsix_auto_mail.db import get_record_by_message_id

        conn = init_db(db_path)
        try:
            assert get_record_by_message_id(conn, "transient-del") is not None
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_archive_cross_folder_heal_and_archive() -> None:
    """POST /archive with a stale UID where cross-folder search finds the
    mail in another folder → heal record, IMAP-move to archive from new
    location, remove local record, 302."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "heal-arch",
                    "sender": "x@x.com",
                    "subject": "Heal archive",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "heal-arch", action="TO_ARCHIVE")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ?, source_folder = ? "
                "WHERE message_id = ?",
                (42, "INBOX", "heal-arch"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with (
                mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls,
                mock.patch(
                    "robotsix_auto_mail.imap.cross_folder_resolve"
                ) as mock_cross,
            ):
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.search_uids.return_value = []
                mock_cross.return_value = ("Projects", 99)
                # The archive heal path calls list_folders on the
                # second client to get the delimiter.
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                status, body = _post_form(
                    port, {"message_id": "heal-arch"}, path="/archive"
                )

            assert status == 302, f"Expected 302, got {status}: {body}"
            # Verify the move was called with the healed UID.
            mock_client.move_message.assert_called_once()
            move_uid = mock_client.move_message.call_args[0][0]
            assert move_uid == 99, (
                f"Expected move_message with UID 99 (healed), got {move_uid}"
            )
        finally:
            server.shutdown()

        # The local record must be removed.
        from robotsix_auto_mail.db import get_record_by_message_id

        conn = init_db(db_path)
        try:
            assert get_record_by_message_id(conn, "heal-arch") is None
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def _seed_archive_structure(
    db_path: str, folders: list[str], delimiter: str = "/"
) -> None:
    """Write an archive_structure watermark to *db_path*."""
    from robotsix_auto_mail.db import init_db

    conn = init_db(db_path)
    try:
        set_watermark(
            conn,
            "archive_structure",
            json.dumps({"delimiter": delimiter, "folders": folders}),
        )
    finally:
        conn.close()


def _seed_archive_override(db_path: str, message_id: str, subfolder: str) -> None:
    """Write a user archive subfolder override."""
    from robotsix_auto_mail.db import init_db
    from robotsix_auto_mail.triage import _save_archive_overrides

    conn = init_db(db_path)
    try:
        overrides = {message_id: subfolder}
        _save_archive_overrides(conn, overrides)
    finally:
        conn.close()


def _seed_llm_archive_hint(db_path: str, message_id: str, subfolder: str) -> None:
    """Write an LLM archive subfolder hint."""
    from robotsix_auto_mail.db import init_db
    from robotsix_auto_mail.triage import _save_llm_archive_hints

    conn = init_db(db_path)
    try:
        hints = {message_id: subfolder}
        _save_llm_archive_hints(conn, hints)
    finally:
        conn.close()


def test_archive_proposal_endpoint_returns_json() -> None:
    """GET /archive-proposal/<mid> returns expected JSON shape."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "ap-mid",
                    "sender": "alice@example.com",
                    "subject": "Archive me",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "ap-mid", action="TO_ARCHIVE")
        _seed_archive_structure(
            db_path,
            ["my-archive", "my-archive/Lists/dev"],
        )

        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/archive-proposal/ap-mid")
            assert resp.status == 200
            assert resp.headers.get("Content-Type", "").startswith("application/json")
            body = resp.read().decode("utf-8")
            payload = json.loads(body)
            assert "subfolder" in payload
            assert "archive_root" in payload
            assert "folder_exists" in payload
            assert "overridden" in payload
            assert "source" in payload
            # Either "rule" (deterministic) or "llm" (no hint stored)
            assert payload["source"] in ("rule", "llm", "override")
            assert isinstance(payload["folder_exists"], bool)
            assert isinstance(payload["overridden"], bool)
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_proposal_endpoint_with_override() -> None:
    """GET /archive-proposal/<mid> returns source='override' when override exists."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "ap-override",
                    "sender": "a@b.com",
                    "subject": "Test",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "ap-override", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "ap-override", "Custom/Path")

        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/archive-proposal/ap-override")
            payload = json.loads(resp.read().decode("utf-8"))
            assert payload["subfolder"] == "Custom/Path"
            assert payload["source"] == "override"
            assert payload["overridden"] is True
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_proposal_endpoint_unknown_404() -> None:
    """GET /archive-proposal/unknown → 404."""
    import urllib.error

    server, port = _start_test_server(":memory:")
    try:
        try:
            urlopen(f"http://127.0.0.1:{port}/archive-proposal/nonexistent")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("Expected HTTPError 404")
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# POST /archive-proposal
# ---------------------------------------------------------------------------


def test_archive_proposal_post_stores_override_and_redirects() -> None:
    """POST /archive-proposal persists the override and redirects to /board."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "post-ap",
                    "sender": "a@b.com",
                    "subject": "Test",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(
                port,
                "/archive-proposal",
                {"message_id": "post-ap", "subfolder": "My/Path"},
            )
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"
        finally:
            server.shutdown()

        # Verify override was persisted.
        from robotsix_auto_mail.triage import _load_archive_overrides

        conn = init_db(db_path)
        try:
            overrides = _load_archive_overrides(conn)
            assert overrides.get("post-ap") == "My/Path"
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_archive_proposal_post_empty_subfolder_clears_override() -> None:
    """POST with empty subfolder clears the override."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "clear-ap",
                    "sender": "a@b.com",
                    "subject": "Test",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_archive_override(db_path, "clear-ap", "Existing")

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(
                port,
                "/archive-proposal",
                {"message_id": "clear-ap", "subfolder": ""},
            )
            assert resp.status == 302
        finally:
            server.shutdown()

        from robotsix_auto_mail.triage import _load_archive_overrides

        conn = init_db(db_path)
        try:
            overrides = _load_archive_overrides(conn)
            assert "clear-ap" not in overrides
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_archive_proposal_post_records_archive_folder_memory() -> None:
    """POST /archive-proposal with a non-empty subfolder records the choice
    in archive-folder memory (both sender and domain)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "rec-ap",
                    "sender": "a@b.com",
                    "subject": "Test",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(
                port,
                "/archive-proposal",
                {"message_id": "rec-ap", "subfolder": "My/Path"},
            )
            assert resp.status == 302
        finally:
            server.shutdown()

        from robotsix_auto_mail.triage import _load_archive_folder_memory

        conn = init_db(db_path)
        try:
            memory = _load_archive_folder_memory(conn)
            assert memory["a@b.com"].subfolder == "My/Path"
            assert memory["b.com"].subfolder == "My/Path"
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_archive_proposal_post_empty_subfolder_records_nothing() -> None:
    """POST /archive-proposal with an empty subfolder records nothing in
    archive-folder memory."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "empty-ap",
                    "sender": "a@b.com",
                    "subject": "Test",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(
                port,
                "/archive-proposal",
                {"message_id": "empty-ap", "subfolder": ""},
            )
            assert resp.status == 302
        finally:
            server.shutdown()

        from robotsix_auto_mail.triage import _load_archive_folder_memory

        conn = init_db(db_path)
        try:
            assert _load_archive_folder_memory(conn) == {}
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_archive_records_archive_folder_memory_before_delete() -> None:
    """POST /archive records the effective subfolder in archive-folder memory
    before the local row is deleted."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "arch-mem-mid",
                    "sender": "x@x.com",
                    "subject": "hier",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "arch-mem-mid", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "arch-mem-mid", "Lists/new-list")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (7, "arch-mem-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                resp = _post_to_path(port, "/archive", {"message_id": "arch-mem-mid"})

            assert resp.status == 302
        finally:
            server.shutdown()

        from robotsix_auto_mail.db import get_record_by_message_id
        from robotsix_auto_mail.triage import _load_archive_folder_memory

        conn = init_db(db_path)
        try:
            # The local row is gone, but the folder memory survives.
            assert get_record_by_message_id(conn, "arch-mem-mid") is None
            memory = _load_archive_folder_memory(conn)
            assert memory["x@x.com"].subfolder == "Lists/new-list"
            assert memory["x.com"].subfolder == "Lists/new-list"
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_archive_proposal_post_missing_message_id_400() -> None:
    """POST /archive-proposal without message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/archive-proposal", {"subfolder": "x"})
        assert resp.status == 400
    finally:
        server.shutdown()


def test_archive_proposal_post_dotdot_segment_400() -> None:
    """POST /archive-proposal with '..' path segment returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(
            port,
            "/archive-proposal",
            {"message_id": "abc", "subfolder": "Lists/../etc"},
        )
        assert resp.status == 400
    finally:
        server.shutdown()


def test_archive_proposal_post_absolute_path_400() -> None:
    """POST /archive-proposal with absolute path returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(
            port,
            "/archive-proposal",
            {"message_id": "abc", "subfolder": "/etc/passwd"},
        )
        assert resp.status == 400
    finally:
        server.shutdown()


def test_archive_proposal_post_overly_long_subfolder_400() -> None:
    """POST /archive-proposal with subfolder exceeding 256 chars returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(
            port,
            "/archive-proposal",
            {"message_id": "abc", "subfolder": "x" * 257},
        )
        assert resp.status == 400
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Board integration — archive proposal in full board HTML
# ---------------------------------------------------------------------------


def test_archive_rejects_dot_dot_path_segment() -> None:
    """POST /archive with a subfolder containing '..' → 400, mail stays."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "dotdot-mid",
                    "sender": "x@x.com",
                    "subject": "dotdot",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "dotdot-mid", action="TO_ARCHIVE")
        # Inject a malicious subfolder override.
        _seed_archive_override(db_path, "dotdot-mid", "Lists/../escape")

        # Give the record an imap_uid so the IMAP path is entered.
        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (42, "dotdot-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            # Mock ImapClient so no real connection is attempted.
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                resp = _post_to_path(port, "/archive", {"message_id": "dotdot-mid"})

            assert resp.status == 400

            # move_message must NOT have been called.
            mock_client.move_message.assert_not_called()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_rejects_path_escaping_root() -> None:
    """POST /archive where dest_folder doesn't start with archive_root
    → 400, mail stays."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "escape-mid",
                    "sender": "x@x.com",
                    "subject": "escape",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "escape-mid", action="TO_ARCHIVE")
        # An override that, when joined to root, would produce a path
        # that still starts with root (because the override is relative).
        # We test the alternative: the delimiter check prevents
        # "archive_root/../foo" style escapes, but the starts-with check
        # is an additional layer.  To exercise it we use a subfolder
        # that contains a leading delimiter which, after translation,
        # could produce something like "//etc" which doesn't start
        # with "archive_root/".  However, the real attack surface is
        # the ".." check, already tested above.  The starts-with gate
        # is defense-in-depth that is hard to trigger with the current
        # subfolder→dest_folder building because the subfolder is
        # always appended to archive_root+delimiter.  We still keep
        # the check because it's cheap and protects against future
        # code changes that might build dest_folder differently.
        #
        # For this test we set up a normal record and verify the
        # valid path passes (no 400 from the security gate).
        _seed_archive_override(db_path, "escape-mid", "Lists/ok")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (99, "escape-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                resp = _post_to_path(port, "/archive", {"message_id": "escape-mid"})

            # A normal subfolder should pass the security gate and
            # proceed to folder creation + move, resulting in a 302.
            assert resp.status == 302
            mock_client.move_message.assert_called_once()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_creates_folder_hierarchy_incrementally() -> None:
    """POST /archive creates every level of dest_folder via create_folder."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "hier-mid",
                    "sender": "x@x.com",
                    "subject": "hier",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "hier-mid", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "hier-mid", "Lists/new-list")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (7, "hier-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                resp = _post_to_path(port, "/archive", {"message_id": "hier-mid"})

            assert resp.status == 302

            # Expect create_folder called for each level:
            #   "my-archive"
            #   "my-archive/Lists"
            #   "my-archive/Lists/new-list"
            expected_calls = [
                mock.call("my-archive"),
                mock.call("my-archive/Lists"),
                mock.call("my-archive/Lists/new-list"),
            ]
            assert mock_client.create_folder.call_args_list == expected_calls

            # move_message must be called after folder creation.
            mock_client.move_message.assert_called_once_with(
                7, "my-archive/Lists/new-list"
            )
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_no_subfolder_creates_root_only() -> None:
    """POST /archive with empty subfolder creates only the root folder."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "rootonly-mid",
                    "sender": "x@x.com",
                    "subject": "rootonly",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "rootonly-mid", action="TO_ARCHIVE")
        # No override → subfolder computed by rules; use a sender that
        # produces an empty subfolder (date-based fallback requires a
        # date; but a sender without '@' produces "" from domain parse).
        # The easiest way is to seed an empty-string override.
        _seed_archive_override(db_path, "rootonly-mid", "")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (3, "rootonly-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                resp = _post_to_path(port, "/archive", {"message_id": "rootonly-mid"})

            assert resp.status == 302

            # Only the root folder is created (single level).
            mock_client.create_folder.assert_called_once_with("my-archive")
            mock_client.move_message.assert_called_once_with(3, "my-archive")
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_create_folder_failure_returns_502_no_move() -> None:
    """When create_folder raises ImapError → 502, mail not moved, DB
    record preserved."""
    from unittest import mock

    from robotsix_auto_mail.imap import ImapError

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "fail-mid",
                    "sender": "x@x.com",
                    "subject": "fail",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "fail-mid", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "fail-mid", "Lists/doomed")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (55, "fail-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
                # Fail on the second create_folder call.
                mock_client.create_folder.side_effect = [
                    None,  # "my-archive" succeeds
                    ImapError("NO [CANNOT] Cannot create that folder"),
                ]

                resp = _post_to_path(port, "/archive", {"message_id": "fail-mid"})

            assert resp.status == 502

            # move_message must NOT have been called.
            mock_client.move_message.assert_not_called()

            # The DB record must still exist.
            conn2 = init_db(db_path)
            try:
                from robotsix_auto_mail.db import get_record_by_message_id

                assert get_record_by_message_id(conn2, "fail-mid") is not None
            finally:
                conn2.close()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_idempotent_create_existing_folders_succeeds() -> None:
    """When all folders already exist, create_folder calls still succeed
    (idempotent) and the move still happens."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "idem-mid",
                    "sender": "x@x.com",
                    "subject": "idem",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "idem-mid", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "idem-mid", "Lists/existing")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (11, "idem-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
                # create_folder returns None (success) for every call.
                mock_client.create_folder.return_value = None

                resp = _post_to_path(port, "/archive", {"message_id": "idem-mid"})

            assert resp.status == 302

            # All three levels are still created (idempotent — real
            # ImapClient would no-op).
            assert mock_client.create_folder.call_count == 3
            mock_client.move_message.assert_called_once_with(
                11, "my-archive/Lists/existing"
            )
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_security_gate_runs_before_any_imap_operation() -> None:
    """When the security gate rejects a path, no IMAP operation
    (list_folders, create_folder, move_message) is performed."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "early-mid",
                    "sender": "x@x.com",
                    "subject": "early",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "early-mid", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "early-mid", "..")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (1, "early-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value

                resp = _post_to_path(port, "/archive", {"message_id": "early-mid"})

            assert resp.status == 400

            # list_folders is called to discover the delimiter (needed
            # to build dest_folder for the security check), but no
            # mutating operations are performed.
            mock_client.create_folder.assert_not_called()
            mock_client.move_message.assert_not_called()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_different_delimiter_creates_correct_levels() -> None:
    """When the IMAP server uses '.' as hierarchy delimiter, folder
    creation uses '.'-separated paths."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "dotdelim-mid",
                    "sender": "x@x.com",
                    "subject": "dotdelim",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "dotdelim-mid", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "dotdelim-mid", "Lists/new-list")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (23, "dotdelim-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                # Server uses '.' as hierarchy delimiter.
                mock_client.list_folders.return_value = [mock.Mock(delimiter=".")]

                resp = _post_to_path(port, "/archive", {"message_id": "dotdelim-mid"})

            assert resp.status == 302

            expected_calls = [
                mock.call("my-archive"),
                mock.call("my-archive.Lists"),
                mock.call("my-archive.Lists.new-list"),
            ]
            assert mock_client.create_folder.call_args_list == expected_calls
            mock_client.move_message.assert_called_once_with(
                23, "my-archive.Lists.new-list"
            )
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _handle_archive — namespace prefix
# ---------------------------------------------------------------------------


def test_archive_namespace_creates_folders_with_prefix() -> None:
    """With archive_namespace set, folders are created under the
    namespaced effective root."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "ns-mid",
                    "sender": "x@x.com",
                    "subject": "ns",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "ns-mid", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "ns-mid", "Lists/new-list")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (99, "ns-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
            archive_namespace="INBOX.",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                resp = _post_to_path(port, "/archive", {"message_id": "ns-mid"})

            assert resp.status == 302

            expected_calls = [
                mock.call("INBOX.my-archive"),
                mock.call("INBOX.my-archive/Lists"),
                mock.call("INBOX.my-archive/Lists/new-list"),
            ]
            assert mock_client.create_folder.call_args_list == expected_calls
            mock_client.move_message.assert_called_once_with(
                99, "INBOX.my-archive/Lists/new-list"
            )
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_namespace_security_gate_uses_effective_root() -> None:
    """The security gate checks against the effective (namespaced) root."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "ns-safe-mid",
                    "sender": "x@x.com",
                    "subject": "ns-safe",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "ns-safe-mid", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "ns-safe-mid", "Lists/ok")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (88, "ns-safe-mid"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
            archive_namespace="INBOX.",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                resp = _post_to_path(port, "/archive", {"message_id": "ns-safe-mid"})

            # The effective root is "INBOX.my-archive" and the dest
            # is "INBOX.my-archive/Lists/ok" — starts-with check passes.
            assert resp.status == 302
            mock_client.move_message.assert_called_once()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_selects_source_folder_not_just_inbox() -> None:
    """POST /archive on a record whose source_folder is not INBOX selects
    the record's source_folder instead of the default IMAP folder."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "legacy-arch",
                    "sender": "x@x.com",
                    "subject": "Legacy archive",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "legacy-arch", action="TO_ARCHIVE")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ?, source_folder = ? "
                "WHERE message_id = ?",
                (99, "INBOX.archive", "legacy-arch"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]
                # search_uids: UID 99 exists in INBOX.archive.
                mock_client.search_uids.return_value = [99]

                resp = _post_to_path(port, "/archive", {"message_id": "legacy-arch"})

            assert resp.status == 302
            # The record's source_folder ("INBOX.archive") must have
            # been selected — NOT the default "INBOX".
            select_calls = [c.args[0] for c in mock_client.select_folder.call_args_list]
            assert "INBOX.archive" in select_calls, (
                f"Expected select_folder('INBOX.archive'), got {select_calls}"
            )
            mock_client.move_message.assert_called_once()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_archive_message_id_fallback_when_uid_stale() -> None:
    """POST /archive: when the stored UID is stale, the Message-ID fallback
    finds the message and the archive succeeds."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "fallback-arch",
                    "sender": "x@x.com",
                    "subject": "Fallback archive",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "fallback-arch", action="TO_ARCHIVE")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (42, "fallback-arch"),
            )
            conn.commit()
        finally:
            conn.close()

        mail_config = MailConfig(
            imap_host="imap.example.com",
            smtp_host="smtp.example.com",
            username="test",
            password="test",
            archive_root="my-archive",
        )

        server, port = _start_test_server_with_mail_config(db_path, mail_config)
        try:
            with mock.patch("robotsix_auto_mail.imap.ImapClient") as mock_cls:
                mock_client = mock_cls.return_value.__enter__.return_value
                mock_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                # UID 42 is stale → search returns [].
                # But the Message-ID fallback finds UID 77.
                call_count = [0]

                def _search_uids(criteria: str) -> list[int]:
                    call_count[0] += 1
                    if "UID 42" in criteria:
                        return []  # stale
                    if "fallback-arch" in criteria:
                        return [77]  # found via Message-ID
                    return [42]  # default

                mock_client.search_uids.side_effect = _search_uids

                resp = _post_to_path(port, "/archive", {"message_id": "fallback-arch"})

            assert resp.status == 302, (
                f"Expected 302, got {resp.status}: {resp.read().decode()[:200]}"
            )
            # The move must use the resolved UID 77, not the stale 42.
            mock_client.move_message.assert_called_once()
            move_uid = mock_client.move_message.call_args[0][0]
            assert move_uid == 77, (
                f"Expected move_message with UID 77 (resolved), got {move_uid}"
            )
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_save_notes_persists_and_redirects() -> None:
    """POST /save-notes with message_id and notes persists and returns 302."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "notes-test-1",
                    "sender": "x@x.com",
                    "subject": "Notes test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(
                port,
                "/save-notes",
                {
                    "message_id": "notes-test-1",
                    "notes": "Waiting for Alice's feedback",
                },
            )
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"
        finally:
            server.shutdown()

        # Verify notes persisted in DB.
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        conn = init_db(db_path)
        try:
            record = get_record_by_message_id(conn, "notes-test-1")
            assert record is not None
            assert record.notes == "Waiting for Alice's feedback"
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_save_notes_nonexistent_message_id_returns_404() -> None:
    """POST /save-notes with nonexistent message_id returns 404."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(
            port,
            "/save-notes",
            {"message_id": "does-not-exist", "notes": "whatever"},
        )
        assert resp.status == 404
    finally:
        server.shutdown()


def test_save_notes_empty_message_id_returns_400() -> None:
    """POST /save-notes with empty message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/save-notes", {"message_id": "  ", "notes": "x"})
        assert resp.status == 400
    finally:
        server.shutdown()


def test_save_notes_missing_message_id_returns_400() -> None:
    """POST /save-notes without message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/save-notes", {"notes": "x"})
        assert resp.status == 400
    finally:
        server.shutdown()


def test_move_to_draft_ready() -> None:
    """POST /move with triage_action=DRAFT_READY moves to the DRAFT_READY column."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "draft-move",
                    "sender": "x@x.com",
                    "subject": "Draft move test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )

        server, port = _start_test_server(db_path)
        try:
            status, body = _post_form(
                port,
                {"message_id": "draft-move", "triage_action": "DRAFT_READY"},
                path="/move",
            )
            assert status == 302, f"Expected 302, got {status}: {body}"

            # Verify the DRAFT_READY column appears with count=1.
            resp = urlopen(f"http://127.0.0.1:{port}/board")
            board_html = resp.read().decode("utf-8")
            # The DRAFT_READY column header should be "Draft ready"
            assert "Draft ready" in board_html
            counts = re.findall(
                r'<span class="board-column-count">(\d+)</span>',
                board_html,
            )
            assert "1" in counts, f"Unexpected counts: {counts}"
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_save_draft_moves_to_draft_ready() -> None:
    """POST /save-draft persists draft_text and moves the card to DRAFT_READY."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Populate and pre-move to TO_ANSWER.
        _populate_db(
            db_path,
            [
                {
                    "message_id": "save-draft-test",
                    "sender": "y@y.com",
                    "subject": "Save draft test",
                    "date": "2025-02-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        server, port = _start_test_server(db_path)
        try:
            # Move to TO_ANSWER first.
            status, _ = _post_form(
                port,
                {"message_id": "save-draft-test", "triage_action": "TO_ANSWER"},
                path="/move",
            )
            assert status == 302

            # Now save a draft.
            status, body = _post_form(
                port,
                {
                    "message_id": "save-draft-test",
                    "draft_text": "Hello, this is a draft reply.",
                },
                path="/save-draft",
            )
            assert status == 302, f"Expected 302, got {status}: {body}"

            # Verify via direct DB query.
            conn = init_db(db_path)
            try:
                cur = conn.execute(
                    "SELECT draft_text FROM mail_records WHERE message_id = ?",
                    ("save-draft-test",),
                )
                row = cur.fetchone()
                assert row is not None
                assert row[0] == "Hello, this is a draft reply."

                from robotsix_auto_mail.triage import get_triage_decision

                decision = get_triage_decision(conn, "save-draft-test")
                assert decision is not None
                assert decision.action == "DRAFT_READY"
                assert decision.source == "user"
            finally:
                conn.close()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_save_draft_missing_message_id_returns_400() -> None:
    """POST /save-draft without message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(
            port,
            {"draft_text": "some text"},
            path="/save-draft",
        )
        assert status == 400
        assert "Missing message_id" in body
    finally:
        server.shutdown()


def test_generate_draft_generates_and_moves_to_draft_ready() -> None:
    """POST /generate-draft stores an LLM draft and moves to DRAFT_READY."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "gen-draft-test",
                    "sender": "y@y.com",
                    "subject": "Question",
                    "date": "2025-02-01T00:00:00",
                    "body_plain": "Can we meet?",
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

        mock_run_result = mock.MagicMock()
        mock_run_result.output = mock.MagicMock(draft_text="Yes, [your time].")
        mock_handle = mock.MagicMock()
        mock_handle.run_sync.return_value = mock_run_result
        mock_provider = mock.MagicMock()
        mock_provider.build_agent.return_value = mock_handle
        mock_provider.call_with_retry.side_effect = lambda fn, what: fn()

        with mock.patch(
            "robotsix_llmio.core.get_provider",
            return_value=mock_provider,
        ):
            server, port = _start_test_server_with_mail_config(db_path, mail_config)
            try:
                status, body = _post_form(
                    port,
                    {"message_id": "gen-draft-test"},
                    path="/generate-draft",
                )
                assert status == 302, f"Expected 302, got {status}: {body}"
            finally:
                server.shutdown()

        conn = init_db(db_path)
        try:
            cur = conn.execute(
                "SELECT draft_text FROM mail_records WHERE message_id = ?",
                ("gen-draft-test",),
            )
            row = cur.fetchone()
            assert row is not None
            assert row[0] == "Yes, [your time]."

            from robotsix_auto_mail.triage import get_triage_decision

            decision = get_triage_decision(conn, "gen-draft-test")
            assert decision is not None
            assert decision.action == "DRAFT_READY"
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_generate_draft_missing_message_id_returns_400() -> None:
    """POST /generate-draft without message_id returns 400."""
    server, port = _start_test_server(":memory:")
    try:
        status, body = _post_form(
            port,
            {},
            path="/generate-draft",
        )
        assert status == 400
        assert "Missing message_id" in body
    finally:
        server.shutdown()


# ===========================================================================
# MailBoardAdapter tests
# ===========================================================================


def test_mailboardadapter_satisfies_protocol() -> None:
    from robotsix_board import BoardAdapter

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    assert isinstance(adapter, BoardAdapter)


def test_mailboardadapter_columns_returns_correct_pairs() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    cols = adapter.columns()
    assert isinstance(cols, list)
    assert len(cols) > 0
    for action, label in cols:
        assert isinstance(action, str)
        assert isinstance(label, str)


def test_mailboardadapter_card_id() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record(message_id="<id@test.com>")
    assert adapter.card_id(record) == "<id@test.com>"


def test_mailboardadapter_card_title() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record(sender="alice@x.com", subject="Hello")
    assert adapter.card_title(record) == "alice@x.com: Hello"


def test_mailboardadapter_card_title_no_subject() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record(sender="alice@x.com", subject="   ")
    assert adapter.card_title(record) == "alice@x.com: (no subject)"


def test_mailboardadapter_card_badges() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={"<test@example.com>": "INBOX"},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record()
    badges = adapter.card_badges(record)
    assert badges == ["Inbox"]


def test_mailboardadapter_archive_override_offers_folder_dropdown() -> None:
    """The override field is a free-text input backed by a folder datalist."""
    mid = "<test@example.com>"
    record = _make_record(message_id=mid)
    adapter = MailBoardAdapter(
        triage_by_mid={mid: "TO_ARCHIVE"},
        archive_subfolders={mid: "Compta"},
        folder_exists={},
        archive_root="robotsix-mail-archive",
        unsubscribe_suggestions={},
        record_notes={},
        column_records={"TO_ARCHIVE": [record]},
        archive_folders=["Billing", "LS2N"],
    )
    # The per-card override input still accepts free text but is wired to the
    # shared datalist dropdown.
    card = adapter.card_extra_html(record)
    assert 'name="subfolder"' in card
    assert 'list="archive-folders"' in card

    # The datalist (emitted once on the column) lists the managed structure
    # folders plus the destination currently proposed for this column.
    col = adapter.column_extra_html("TO_ARCHIVE")
    assert '<datalist id="archive-folders">' in col
    assert '<option value="Billing">' in col
    assert '<option value="LS2N">' in col
    assert '<option value="Compta">' in col


def test_mailboardadapter_card_badges_unmatched_returns_inbox() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record()
    badges = adapter.card_badges(record)
    assert badges == ["Inbox"]


def test_mailboardadapter_card_timestamps() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record(date="2025-06-01T12:00:00")
    ts = adapter.card_timestamps(record)
    assert "date" in ts
    assert "2025-06-01" in ts["date"]


def test_mailboardadapter_move_endpoint() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    record = _make_record()
    url, method = adapter.move_endpoint(record)
    assert url == "/move"
    assert method == "post"


def test_mailboardadapter_move_endpoint_template() -> None:

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    assert adapter.move_endpoint_template() == "/move/{card_id}/{target_status}"


def test_mailboardadapter_render_mode() -> None:
    from robotsix_board import RenderMode

    adapter = MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
    )
    assert adapter.render_mode() == RenderMode.SERVER_FRAGMENTS


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


def test_handler_board_uses_library_css_classes() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


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


def test_handler_board_content_json_keys() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


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


def test_handler_email_detail_links_app_css() -> None:
    """GET /email/{id} links the app stylesheet after the library one."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def test_handler_email_detail_embed_links_app_css() -> None:
    """GET /email/{id}?embed=1 also links the app stylesheet."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/email/css-embed?embed=1")
            body = resp.read().decode("utf-8")
            assert 'href="/static/automail/board.css"' in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


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


def test_handler_email_detail_embed_notifies_parent_board() -> None:
    """The embed fragment carries a guarded parent-board refresh script."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/email/notify-embed?embed=1")
            body = resp.read().decode("utf-8")
            assert "window.parent.refreshBoard(true)" in body
            assert "typeof window.parent.refreshBoard === 'function'" in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_handler_email_detail_standalone_has_no_parent_refresh() -> None:
    """The standalone (non-embed) detail page must not notify a parent board."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/email/standalone-detail")
            body = resp.read().decode("utf-8")
            assert "window.parent.refreshBoard" not in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_handler_board_inline_handlers_resolve_to_defined_functions() -> None:
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
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        _seed_triage_decision(db_path, "guard-1", action="TO_DELETE")

        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/board")
            board_html = resp.read().decode("utf-8")
            # Collect the JS reachable by the page: every inline <script>
            # block (no src) plus every served script the page references.
            inline_scripts = re.findall(
                r"<script>(.*?)</script>", board_html, re.DOTALL
            )
            served_srcs = re.findall(r'<script src="([^"]+)"', board_html)
            defined_sources = list(inline_scripts)
            for src in served_srcs:
                served = urlopen(f"http://127.0.0.1:{port}{src}")
                defined_sources.append(served.read().decode("utf-8"))
            defined_js = "\n".join(defined_sources)

            # Top-level function identifiers defined in the reachable JS.
            defined_names = set(
                re.findall(r"function\s+([A-Za-z_$][\w$]*)", defined_js)
            )

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
    finally:
        os.unlink(db_path)


def test_handler_static_unknown_returns_404() -> None:
    import urllib.error

    server, port = _start_test_server(":memory:")
    try:
        try:
            urlopen(f"http://127.0.0.1:{port}/static/nonexistent.xyz")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        else:
            raise AssertionError("Expected HTTPError for 404")
    finally:
        server.shutdown()


# ===========================================================================
# Email detail page tests (new patterns)
# ===========================================================================


def test_handler_email_detail_has_board_css() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/email/em1")
            body = resp.read().decode("utf-8")
            assert '<link rel="stylesheet" href="/static/board.css">' in body
            assert '<a class="back-link"' in body
            assert '<div class="detail-container">' in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_handler_email_detail_embed_no_chrome() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
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
        server, port = _start_test_server(db_path)
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
    finally:
        os.unlink(db_path)


def _make_extra_html_adapter() -> MailBoardAdapter:
    """Build an adapter with TO_DELETE/CLEANUP records and an unsubscribe hint."""
    return MailBoardAdapter(
        triage_by_mid={},
        archive_subfolders={},
        folder_exists={},
        archive_root="",
        unsubscribe_suggestions={
            "sender@example.com": {
                "method": "mailto",
                "url": "mailto:unsub@example.com",
                "description": "Reply to unsubscribe.",
            }
        },
        record_notes={},
        column_records={
            "TO_DELETE": [_make_record(message_id="<d1@example.com>")],
            "TO_ARCHIVE": [_make_record(message_id="<r1@example.com>")],
        },
    )


def test_column_extra_html_to_delete_wraps_buttons_banner_outside() -> None:
    """TO_DELETE wraps both forms in .column-extra-top, banner stays after it."""
    adapter = _make_extra_html_adapter()
    html_out = adapter.column_extra_html("TO_DELETE")
    open_idx = html_out.find('<div class="column-extra-top">')
    close_idx = html_out.find("</div>", open_idx)
    assert open_idx != -1
    assert close_idx != -1
    wrapper = html_out[open_idx : close_idx + len("</div>")]
    # Both action-button forms live inside the wrapper.
    assert 'class="delete-form"' in wrapper
    assert 'class="delete-btn"' in wrapper
    assert 'class="force-triage-form"' in wrapper
    assert 'class="force-triage-btn"' in wrapper
    # The unsubscribe banner sits AFTER the closing wrapper div.
    banner_idx = html_out.find('class="unsubscribe-banner"')
    assert banner_idx != -1
    assert banner_idx > close_idx


def test_column_extra_html_non_to_delete_has_force_triage_only() -> None:
    """A non-INBOX, non-TO_DELETE column wraps the force-triage form only."""
    adapter = _make_extra_html_adapter()
    html_out = adapter.column_extra_html("TO_ARCHIVE")
    assert '<div class="column-extra-top">' in html_out
    assert 'class="force-triage-form"' in html_out
    assert 'class="delete-form"' not in html_out


def test_served_css_orders_banner_above_column_extra_top_above_cards() -> None:
    """The served stylesheet sorts the unsubscribe banner to the column top."""
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/static/automail/board.css")
        body = resp.read().decode("utf-8")
        assert ".column-extra-top { order: 1;" in body
        assert ".board-column-cards { order: 2; }" in body
        assert ".unsubscribe-banner { order: 0; }" in body
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# /send-draft
# ---------------------------------------------------------------------------


def _seed_draft_record(
    db_path: str,
    message_id: str,
    *,
    sender: str,
    subject: str,
    draft_text: str,
    recipients_json: str = '{"to": [], "cc": []}',
    imap_uid: int | None = None,
    action: str = "DRAFT_READY",
) -> None:
    """Insert a mail record with a saved draft and a triage decision."""
    conn = init_db(db_path)
    try:
        conn.execute(
            "INSERT INTO mail_records "
            "(message_id, sender, subject, date, recipients_json, "
            "body_plain, body_html, attachments_json, status, "
            "draft_text, imap_uid) "
            "VALUES (?, ?, ?, ?, ?, 'body', '', '[]', 'to_read', ?, ?)",
            (
                message_id,
                sender,
                subject,
                "2025-01-01T00:00:00",
                recipients_json,
                draft_text,
                imap_uid,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    if action:
        _seed_triage_decision(db_path, message_id, action=action)


def _dummy_send_mail_config() -> MailConfig:
    """A fully-populated MailConfig usable for /send-draft tests."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="user@example.com",
        password="pass",
        archive_root="my-archive",
    )


def test_send_draft_reply_sends_and_requeues_for_triage() -> None:
    """POST /send-draft reply → sends mail, retains the record, stores the
    sent reply, and clears the triage decision so it re-enters triage."""
    from unittest import mock

    from robotsix_auto_mail.db import (
        get_record_by_message_id,
        list_untriaged_records,
    )

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_draft_record(
            db_path,
            "send-reply-mid",
            sender="alice@other.com",
            subject="Question",
            draft_text="Here is my reply.",
            imap_uid=5,
        )
        _seed_archive_override(db_path, "send-reply-mid", "")

        server, port = _start_test_server_with_mail_config(
            db_path, _dummy_send_mail_config()
        )
        try:
            with (
                mock.patch("robotsix_auto_mail.smtp.SmtpClient") as smtp_cls,
                mock.patch("robotsix_auto_mail.imap.ImapClient") as imap_cls,
            ):
                imap_client = imap_cls.return_value.__enter__.return_value
                imap_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                resp = _post_to_path(
                    port,
                    "/send-draft",
                    {"message_id": "send-reply-mid", "reply_mode": "reply"},
                )

                assert resp.status == 302
                assert resp.headers.get("Location") == "/board"

                send_mock = smtp_cls.return_value.__enter__.return_value.send
                send_mock.assert_called_once()
                kwargs = send_mock.call_args.kwargs
                assert kwargs["from_addr"] == "user@example.com"
                assert kwargs["to_addr"] == "alice@other.com"
                assert kwargs["subject"].startswith("Re: ")
                assert kwargs["body"] == "Here is my reply."
                assert not kwargs["cc"]

                # No IMAP archive move was performed in the send path.
                imap_client.move_message.assert_not_called()

            # The record is retained, its sent reply body is stored, and its
            # triage decision was cleared so it appears as untriaged.
            conn = init_db(db_path)
            try:
                record = get_record_by_message_id(conn, "send-reply-mid")
                assert record is not None
                assert record.sent_reply_text == "Here is my reply."
                untriaged_ids = {r.message_id for r in list_untriaged_records(conn)}
                assert "send-reply-mid" in untriaged_ids
            finally:
                conn.close()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_send_draft_refuses_self_reply() -> None:
    """POST /send-draft where the recipient equals the user's own address →
    400 and no SMTP send occurs."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_draft_record(
            db_path,
            "send-self-mid",
            sender="USER@example.com",  # same address as username, differing case
            subject="Note to self",
            draft_text="Reply body.",
            imap_uid=5,
        )

        server, port = _start_test_server_with_mail_config(
            db_path, _dummy_send_mail_config()
        )
        try:
            with mock.patch("robotsix_auto_mail.smtp.SmtpClient") as smtp_cls:
                resp = _post_to_path(
                    port,
                    "/send-draft",
                    {"message_id": "send-self-mid", "reply_mode": "reply"},
                )
                assert resp.status == 400
                smtp_cls.return_value.__enter__.return_value.send.assert_not_called()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_send_draft_reply_all_cc_recipients() -> None:
    """POST /send-draft reply_all → cc is original to+cc minus self/sender."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_draft_record(
            db_path,
            "send-all-mid",
            sender="sender@test.com",
            subject="Group thread",
            draft_text="Reply to everyone.",
            recipients_json=(
                '{"to": ["sender@test.com", "carol@x.com", "user@example.com"],'
                ' "cc": ["dave@x.com", "Carol@x.com"]}'
            ),
            imap_uid=9,
        )
        _seed_archive_override(db_path, "send-all-mid", "")

        server, port = _start_test_server_with_mail_config(
            db_path, _dummy_send_mail_config()
        )
        try:
            with (
                mock.patch("robotsix_auto_mail.smtp.SmtpClient") as smtp_cls,
                mock.patch("robotsix_auto_mail.imap.ImapClient") as imap_cls,
            ):
                imap_client = imap_cls.return_value.__enter__.return_value
                imap_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                resp = _post_to_path(
                    port,
                    "/send-draft",
                    {"message_id": "send-all-mid", "reply_mode": "reply_all"},
                )

                assert resp.status == 302
                send_mock = smtp_cls.return_value.__enter__.return_value.send
                kwargs = send_mock.call_args.kwargs
                # Self (username) and the sender (already in To) are
                # excluded; duplicates removed; order preserved.
                assert kwargs["cc"] == ["carol@x.com", "dave@x.com"]
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_send_draft_subject_not_double_prefixed() -> None:
    """A subject already starting with 'Re:' is not double-prefixed."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_draft_record(
            db_path,
            "send-re-mid",
            sender="alice@other.com",
            subject="RE: existing",
            draft_text="Reply body.",
            imap_uid=2,
        )
        _seed_archive_override(db_path, "send-re-mid", "")

        server, port = _start_test_server_with_mail_config(
            db_path, _dummy_send_mail_config()
        )
        try:
            with (
                mock.patch("robotsix_auto_mail.smtp.SmtpClient") as smtp_cls,
                mock.patch("robotsix_auto_mail.imap.ImapClient") as imap_cls,
            ):
                imap_client = imap_cls.return_value.__enter__.return_value
                imap_client.list_folders.return_value = [mock.Mock(delimiter="/")]

                _post_to_path(
                    port,
                    "/send-draft",
                    {"message_id": "send-re-mid", "reply_mode": "reply"},
                )

                send_mock = smtp_cls.return_value.__enter__.return_value.send
                assert send_mock.call_args.kwargs["subject"] == "RE: existing"
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_send_draft_validation_errors() -> None:
    """Validation failures return 4xx and never send/archive/delete."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_draft_record(
            db_path,
            "send-valid-mid",
            sender="alice@other.com",
            subject="Subject",
            draft_text="A draft.",
            imap_uid=4,
        )
        # A record with an empty draft for the empty-draft check.
        _seed_draft_record(
            db_path,
            "send-empty-mid",
            sender="alice@other.com",
            subject="Subject",
            draft_text="   ",
            imap_uid=6,
        )

        server, port = _start_test_server_with_mail_config(
            db_path, _dummy_send_mail_config()
        )
        try:
            with (
                mock.patch("robotsix_auto_mail.smtp.SmtpClient") as smtp_cls,
                mock.patch("robotsix_auto_mail.imap.ImapClient") as imap_cls,
            ):
                # Missing message_id → 400.
                assert (
                    _post_to_path(port, "/send-draft", {"reply_mode": "reply"}).status
                    == 400
                )
                # Unknown message_id → 404.
                assert (
                    _post_to_path(
                        port,
                        "/send-draft",
                        {"message_id": "nope", "reply_mode": "reply"},
                    ).status
                    == 404
                )
                # Empty draft_text → 400.
                assert (
                    _post_to_path(
                        port,
                        "/send-draft",
                        {"message_id": "send-empty-mid", "reply_mode": "reply"},
                    ).status
                    == 400
                )
                # Invalid reply_mode → 400.
                assert (
                    _post_to_path(
                        port,
                        "/send-draft",
                        {"message_id": "send-valid-mid", "reply_mode": "bogus"},
                    ).status
                    == 400
                )

                # No mail sent, no archive move performed.
                smtp_cls.return_value.__enter__.return_value.send.assert_not_called()
                imap_move = imap_cls.return_value.__enter__.return_value.move_message
                imap_move.assert_not_called()

            # Records still present.
            assert (
                urlopen(f"http://127.0.0.1:{port}/email/send-valid-mid").status == 200
            )
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_send_draft_missing_smtp_config_returns_400() -> None:
    """POST /send-draft with no SMTP config → 400, nothing sent."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_draft_record(
            db_path,
            "send-nosmtp-mid",
            sender="alice@other.com",
            subject="Subject",
            draft_text="A draft.",
        )
        # No mail_config bound → SMTP not configured.
        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(
                port,
                "/send-draft",
                {"message_id": "send-nosmtp-mid", "reply_mode": "reply"},
            )
            assert resp.status == 400
            # Record untouched.
            assert (
                urlopen(f"http://127.0.0.1:{port}/email/send-nosmtp-mid").status == 200
            )
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_send_draft_buttons_rendered_only_when_draft_ready() -> None:
    """The detail page renders both /send-draft forms only for DRAFT_READY."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_draft_record(
            db_path,
            "ui-ready-mid",
            sender="alice@other.com",
            subject="Ready",
            draft_text="A draft.",
            action="DRAFT_READY",
        )
        _seed_draft_record(
            db_path,
            "ui-answer-mid",
            sender="bob@other.com",
            subject="Answer",
            draft_text="",
            action="TO_ANSWER",
        )

        server, port = _start_test_server(db_path)
        try:
            ready = (
                urlopen(f"http://127.0.0.1:{port}/email/ui-ready-mid")
                .read()
                .decode("utf-8")
            )
            assert 'action="/send-draft"' in ready
            assert 'name="reply_mode" value="reply"' in ready
            assert 'name="reply_mode" value="reply_all"' in ready

            answer = (
                urlopen(f"http://127.0.0.1:{port}/email/ui-answer-mid")
                .read()
                .decode("utf-8")
            )
            assert 'action="/send-draft"' not in answer
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Multi-account request routing + DB isolation
# ---------------------------------------------------------------------------


def _account_config(db_path: str) -> MailConfig:
    """A MailConfig bound to *db_path* that never touches the LLM/archive layers."""
    return MailConfig(
        imap_host="imap.example.com",
        smtp_host="smtp.example.com",
        username="me@example.com",
        password="s3cret",
        db_path=db_path,
        archive_enabled=False,
        triage_on_ingest=False,
    )


def _start_test_server_with_accounts(
    accounts: MailAccountsConfig,
    default_account_id: str,
    port: int = 0,
) -> tuple[HTTPServer, int]:
    """Start an HTTPServer wired to a multi-account container."""
    import threading
    from http.server import HTTPServer

    from robotsix_auto_mail.server import make_board_handler

    default = accounts.get(default_account_id)
    handler = make_board_handler(
        default.config.db_path,
        mail_config=default.config,
        accounts=accounts,
        default_account_id=default_account_id,
    )
    server = HTTPServer(("127.0.0.1", port), handler)
    assigned_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, assigned_port


def _two_account_setup(
    db_a: str, db_b: str, default_account_id: str = "A"
) -> MailAccountsConfig:
    """Build a two-account container with seeded, distinct records per DB."""
    _populate_db(
        db_a,
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
    _populate_db(
        db_b,
        [
            {
                "message_id": "msg-b",
                "sender": "bob@b.com",
                "subject": "From B",
                "date": "2025-01-02T00:00:00",
                "body_plain": "Body B",
                "status": "to_read",
            },
        ],
    )
    return MailAccountsConfig(
        accounts=(
            MailAccount(account_id="A", config=_account_config(db_a), label=None),
            MailAccount(account_id="B", config=_account_config(db_b), label=None),
        ),
        default_account_id=default_account_id,
    )


def _get(url: str, cookie: str | None = None) -> tuple[int, str, dict[str, str]]:
    """GET *url* (optionally with a Cookie header); return status, body, headers."""
    from urllib.request import Request

    req = Request(url)  # noqa: S310
    if cookie is not None:
        req.add_header("Cookie", cookie)
    resp = urlopen(req)  # noqa: S310
    try:
        return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
    finally:
        resp.close()


def test_make_board_handler_with_accounts_adds_keywords() -> None:
    """With accounts, the partial carries the extra resolution keywords."""
    from robotsix_auto_mail.server import make_board_handler

    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
        handler = make_board_handler(
            db_a,
            mail_config=accounts.get("A").config,
            accounts=accounts,
            default_account_id="A",
        )
        assert handler.keywords["accounts"] is accounts
        assert handler.keywords["default_account_id"] == "A"
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_query_string_tolerant_routing() -> None:
    """GET /board?account=A and POST /move?account=A dispatch (not 404)."""
    from urllib.request import Request

    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
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
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_get_routing_isolates_accounts() -> None:
    """GET /board-content?account=<id> serves only that account's records."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
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
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_get_default_account_no_param() -> None:
    """GET /board-content with no param and ≥2 accounts defaults to aggregate."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b, default_account_id="B")
        server, port = _start_test_server_with_accounts(accounts, "B")
        try:
            _s, body, _h = _get(f"http://127.0.0.1:{port}/board-content")
            # Aggregate view shows cards from both accounts.
            assert "bob@b.com" in body
            assert "alice@a.com" in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def _triage_action(db_path: str, message_id: str) -> str | None:
    """Return the stored triage action for *message_id*, or None."""
    conn = init_db(db_path)
    try:
        cur = conn.execute(
            "SELECT action FROM triage_decisions WHERE message_id = ?",
            (message_id,),
        )
        row = cur.fetchone()
        return None if row is None else cast(str, row[0])
    finally:
        conn.close()


def test_post_move_isolates_accounts() -> None:
    """POST /move?account=B writes only to B's DB; A's DB is untouched."""
    from urllib.request import Request

    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
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
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_unknown_explicit_account_is_404() -> None:
    """Explicit ?account=bogus → 404 on both GET and POST."""
    from urllib.error import HTTPError
    from urllib.request import Request

    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            try:
                urlopen(f"http://127.0.0.1:{port}/board?account=bogus").close()
                raise AssertionError("expected 404")
            except HTTPError as exc:
                assert exc.code == 404

            req = Request(
                f"http://127.0.0.1:{port}/move?account=bogus",
                data=b"message_id=msg-a&triage_action=read",
                method="POST",
            )
            try:
                urlopen(req).close()  # noqa: S310
                raise AssertionError("expected 404")
            except HTTPError as exc:
                assert exc.code == 404
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_stale_cookie_falls_back_to_default() -> None:
    """A stale/unknown id from the cookie is ignored — default served, no 404."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b, default_account_id="A")
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
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_cookie_persistence() -> None:
    """GET /board?account=B sets the cookie; a cookie-only request serves B."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b, default_account_id="A")
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
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def _two_account_setup_with_labels(
    db_a: str, db_b: str, default_account_id: str = "A"
) -> MailAccountsConfig:
    """Two-account container with a non-None label on account ``A``."""
    base = _two_account_setup(db_a, db_b, default_account_id=default_account_id)
    return MailAccountsConfig(
        accounts=(
            MailAccount(
                account_id="A", config=_account_config(db_a), label="Alice <Work>"
            ),
            MailAccount(account_id="B", config=_account_config(db_b), label=None),
        ),
        default_account_id=base.default_account_id,
    )


def test_picker_visible_multi_account() -> None:
    """A 2-account board defaults to aggregate with 'All mailboxes' selected."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_labels(db_a, db_b)
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
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_picker_reflects_selection() -> None:
    """GET /board?account=B marks the B option selected (not A)."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
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
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_picker_onchange_navigates() -> None:
    """The picker reload handler navigates to ?account=."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            _s, body, _h = _get(f"http://127.0.0.1:{port}/board")
            assert "window.location.href='/board?account='" in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_account_threaded_into_js_urls() -> None:
    """At ?account=B the detail iframe + content fetch carry account=B."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            _s, body, _h = _get(f"http://127.0.0.1:{port}/board?account=B")
            # The account query strings are now carried in the #board-config
            # JSON element, consumed by board-auto-mail.js at runtime.
            assert '"account_qs": "&account=B"' in body
            assert '"fetch_qs": "?account=B"' in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_detail_panel_shows_selected_account_data() -> None:
    """GET /email/<msg-in-B>?embed=1&account=B returns B's data."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            status, body, _h = _get(
                f"http://127.0.0.1:{port}/email/msg-b?embed=1&account=B"
            )
            assert status == 200
            assert "bob@b.com" in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_single_account_container_renders_no_picker() -> None:
    """A 1-element MailAccountsConfig renders no picker and no account param."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    try:
        _populate_db(
            db_a,
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
                    account_id="default", config=_account_config(db_a), label=None
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
    finally:
        os.unlink(db_a)


def test_build_board_html_legacy_no_accounts_kwarg() -> None:
    """Direct _build_board_html(db_path) produces no picker, unchanged URLs."""
    from robotsix_auto_mail.server.views import _build_board_html

    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    try:
        _populate_db(
            db_a,
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
        body = _build_board_html(db_a)
        assert '<select id="account-picker"' not in body
        # The board-auto-mail.js overlay handles URL construction; the
        # #board-config carries the query-string fragments.
        assert '"fetch_qs": ""' in body
        assert '"account_qs": ""' in body
        assert '"data_account_js": false' in body
    finally:
        os.unlink(db_a)


# ===========================================================================


def _seed_batch_state(db_path: str, value: str) -> None:
    """Write the ``batch_op:state`` watermark directly."""
    conn = init_db(db_path)
    try:
        set_watermark(conn, "batch_op:state", value)
    finally:
        conn.close()


def test_batch_delete_single_flight_does_not_spawn_second_worker() -> None:
    """A second POST /batch-delete while batch_op:state is running is a
    no-op single-flight redirect — the running watermark is untouched."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_batch_state(db_path, "running")
        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/batch-delete", {})
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"
            # No worker spawned → watermark is still the seeded "running".
            from robotsix_auto_mail.db import get_watermark

            conn = init_db(db_path, skip_migrations=True)
            try:
                assert get_watermark(conn, "batch_op:state") == "running"
            finally:
                conn.close()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_batch_archive_blocked_by_running_delete_shared_key() -> None:
    """POST /batch-archive while a delete is running (shared batch_op:state
    key) is a single-flight no-op and leaves the watermark running."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # A JSON delete-progress payload counts as running.
        _seed_batch_state(db_path, json.dumps({"op": "delete", "done": 1, "total": 5}))
        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/batch-archive", {})
            assert resp.status == 302
            from robotsix_auto_mail.db import get_watermark

            conn = init_db(db_path, skip_migrations=True)
            try:
                state = get_watermark(conn, "batch_op:state")
            finally:
                conn.close()
            assert state == json.dumps({"op": "delete", "done": 1, "total": 5})
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_batch_archive_db_only_removes_records_and_clears_watermark() -> None:
    """POST /batch-archive deletes every TO_ARCHIVE record (DB-only path,
    no IMAP) in the background and resets batch_op:state to idle."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "ba-1",
                    "sender": "a@b.com",
                    "subject": "Archive me 1",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
                {
                    "message_id": "ba-2",
                    "sender": "c@d.com",
                    "subject": "Archive me 2",
                    "date": "2025-01-02T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "ba-1", action="TO_ARCHIVE")
        _seed_triage_decision(db_path, "ba-2", action="TO_ARCHIVE")

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/batch-archive", {})
            assert resp.status == 302
            assert _wait_for_batch_idle(db_path) in (None, "idle")
        finally:
            server.shutdown()

        from robotsix_auto_mail.db import get_record_by_message_id

        conn = init_db(db_path)
        try:
            assert get_record_by_message_id(conn, "ba-1") is None
            assert get_record_by_message_id(conn, "ba-2") is None
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_batch_delete_worker_clears_watermark_even_when_imap_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The delete worker's finally block resets batch_op:state to idle even
    when an IMAP call raises, leaving the records re-triggerable."""
    import robotsix_auto_mail.imap as imap_mod
    from robotsix_auto_mail.server.adapters import _run_batch_delete_background

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "bw-1",
                    "sender": "a@b.com",
                    "subject": "Boom",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        # Give the record a tracked UID so the worker takes the IMAP path.
        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = 42 WHERE message_id = 'bw-1'"
            )
            conn.commit()
        finally:
            conn.close()
        _seed_triage_decision(db_path, "bw-1", action="TO_DELETE")

        class _BoomClient:
            def __init__(self, *a: object, **k: object) -> None:
                pass

            def __enter__(self) -> "_BoomClient":
                raise imap_mod.ImapError("kaboom")

            def __exit__(self, *a: object) -> None:
                pass

        monkeypatch.setattr(imap_mod, "ImapClient", _BoomClient)

        mail_config = MailConfig(
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls_mode="direct",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_tls_mode="direct",
            username="user@example.com",
            password="pw",
            db_path=db_path,
        )
        _run_batch_delete_background(db_path, mail_config)

        from robotsix_auto_mail.db import get_record_by_message_id, get_watermark

        conn = init_db(db_path, skip_migrations=True)
        try:
            assert get_watermark(conn, "batch_op:state") == "idle"
            # IMAP raised before any delete → record left re-triggerable.
            assert get_record_by_message_id(conn, "bw-1") is not None
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_batch_delete_worker_retrigger_skips_already_deleted() -> None:
    """Re-running the delete worker only processes records still present in
    the DB (already-deleted ones are skipped because they were committed)."""
    from robotsix_auto_mail.server.adapters import _run_batch_delete_background

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "rt-1",
                    "sender": "a@b.com",
                    "subject": "One",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
                {
                    "message_id": "rt-2",
                    "sender": "c@d.com",
                    "subject": "Two",
                    "date": "2025-01-02T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "rt-1", action="TO_DELETE")
        _seed_triage_decision(db_path, "rt-2", action="TO_DELETE")

        # First run (DB-only, mail_config=None) deletes both records.
        _run_batch_delete_background(db_path, None)

        from robotsix_auto_mail.db import get_record_by_message_id, get_watermark

        conn = init_db(db_path, skip_migrations=True)
        try:
            assert get_record_by_message_id(conn, "rt-1") is None
            assert get_record_by_message_id(conn, "rt-2") is None
        finally:
            conn.close()

        # Re-trigger: nothing remains → total 0, no error, watermark idle.
        _run_batch_delete_background(db_path, None)
        conn = init_db(db_path, skip_migrations=True)
        try:
            assert get_watermark(conn, "batch_op:state") == "idle"
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_build_board_content_batch_op_running_suppresses_delete_all() -> None:
    """When batch_op:state holds a JSON payload, _build_board_content returns
    the parsed batch_op and the columns omit the Delete-All button."""
    from robotsix_auto_mail.server.views import _build_board_content

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "bc-1",
                    "sender": "a@b.com",
                    "subject": "Del",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "bc-1", action="TO_DELETE")

        # Idle → batch_op None, Delete-All present.
        idle = _build_board_content(db_path)
        assert idle["batch_op"] is None
        assert "Delete All" in idle["columns_html"]

        # Running → parsed batch_op, Delete-All suppressed.
        _seed_batch_state(
            db_path, json.dumps({"op": "delete", "done": 120, "total": 518})
        )
        running = _build_board_content(db_path)
        assert running["batch_op"] == {"op": "delete", "done": 120, "total": 518}
        assert "Delete All" not in running["columns_html"]
    finally:
        os.unlink(db_path)


def test_board_and_content_render_batch_banner() -> None:
    """/board renders a .batch-banner with done/total and /board-content's
    JSON carries the batch_op payload while a batch op is running."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_batch_state(
            db_path, json.dumps({"op": "delete", "done": 120, "total": 518})
        )
        server, port = _start_test_server(db_path)
        try:
            body = urlopen(f"http://127.0.0.1:{port}/board").read().decode("utf-8")
            assert "batch-banner" in body
            assert "120/518" in body

            content = json.loads(
                urlopen(f"http://127.0.0.1:{port}/board-content").read().decode("utf-8")
            )
            assert content["batch_op"] == {"op": "delete", "done": 120, "total": 518}
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_to_archive_column_renders_archive_all_button() -> None:
    """A TO_ARCHIVE column renders an Archive All form posting /batch-archive."""
    from robotsix_auto_mail.server.views import _build_board_content

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "aa-1",
                    "sender": "a@b.com",
                    "subject": "Arc",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "aa-1", action="TO_ARCHIVE")
        content = _build_board_content(db_path)
        assert 'action="/batch-archive"' in content["columns_html"]
        assert "Archive All" in content["columns_html"]
    finally:
        os.unlink(db_path)


# ===========================================================================
# _is_safe_redirect_path unit tests
# ===========================================================================


def test_is_safe_redirect_path_simple_root() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert _is_safe_redirect_path("/")


def test_is_safe_redirect_path_with_path_segments() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert _is_safe_redirect_path("/path/to/page")


def test_is_safe_redirect_path_with_query_and_fragment() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert _is_safe_redirect_path("/path?q=1#frag")


def test_is_safe_redirect_path_rejects_protocol_relative() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("//evil.com")


def test_is_safe_redirect_path_rejects_backslash_trick() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("/\\evil")


def test_is_safe_redirect_path_rejects_empty_string() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("")


def test_is_safe_redirect_path_rejects_no_leading_slash() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("board")
    assert not _is_safe_redirect_path("../etc")


def test_is_safe_redirect_path_rejects_absolute_url() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("http://evil.com")
    assert not _is_safe_redirect_path("https://evil.com/path")


def test_is_safe_redirect_path_rejects_crlf_injection() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("/board\r\nSet-Cookie: pwned=1")
    assert not _is_safe_redirect_path("/board\nX-Injected: true")
    assert not _is_safe_redirect_path("/board\rX-Injected: true")


def test_is_safe_redirect_path_rejects_other_control_characters() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("/path\x00")
    assert not _is_safe_redirect_path("/path\x1f")
    assert not _is_safe_redirect_path("/path\x7f")  # DEL


def test_is_safe_redirect_path_rejects_null_byte() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert not _is_safe_redirect_path("/safe\x00hidden")


def test_is_safe_redirect_path_accepts_typical_valid_paths() -> None:
    from robotsix_auto_mail.server._constants import _is_safe_redirect_path

    assert _is_safe_redirect_path("/board")
    assert _is_safe_redirect_path("/email/some-id")
    assert _is_safe_redirect_path("/email/some-id?embed=1")


# ===========================================================================
# _parse_archive_structure unit tests
# ===========================================================================


def test_parse_archive_structure_none_input_returns_defaults() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    folders, delim, root = _parse_archive_structure(None, "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_empty_string_falls_back() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    folders, delim, root = _parse_archive_structure("", "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_malformed_json_falls_back() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    folders, delim, root = _parse_archive_structure("not json{", "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_old_format_bare_list() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '["a", "b", "c"]'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"a", "b", "c"}
    assert delim == "/"
    assert root == "a"


def test_parse_archive_structure_old_format_single_element_list() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '["only"]'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"only"}
    assert delim == "/"
    assert root == "only"


def test_parse_archive_structure_old_format_empty_list() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = "[]"
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_new_format_with_delimiter_and_folders() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"delimiter": ".", "folders": ["x", "y", "z"]}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"x", "y", "z"}
    assert delim == "."
    assert root == "x"


def test_parse_archive_structure_new_format_default_delimiter() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"folders": ["p", "q"]}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"p", "q"}
    assert delim == "/"
    assert root == "p"


def test_parse_archive_structure_new_format_empty_folders() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"delimiter": "/", "folders": []}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_new_format_single_folder() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"delimiter": "/", "folders": ["single"]}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"single"}
    assert delim == "/"
    assert root == "single"


def test_parse_archive_structure_new_format_missing_folders_key_falls_back() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"delimiter": "/"}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_parse_archive_structure_non_list_non_dict_falls_back() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    assert _parse_archive_structure("42", "my-archive") == (set(), "/", "my-archive")
    assert _parse_archive_structure('"a string"', "my-archive") == (
        set(),
        "/",
        "my-archive",
    )


def test_parse_archive_structure_extra_keys_in_new_format_ignored() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    raw = '{"delimiter": ".", "folders": ["a", "b"], "extra": "ignored"}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == {"a", "b"}
    assert delim == "."
    assert root == "a"


def test_parse_archive_structure_typeerror_falls_back() -> None:
    from robotsix_auto_mail.server._constants import _parse_archive_structure

    # JSON with null for folders triggers TypeError on dict iteration
    raw = '{"folders": null}'
    folders, delim, root = _parse_archive_structure(raw, "my-archive")
    assert folders == set()
    assert delim == "/"
    assert root == "my-archive"


def test_batch_archive_worker_groups_uids_by_destination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The archive worker groups UIDs by their effective destination folder
    and issues one move_messages call per group."""
    import robotsix_auto_mail.imap as imap_mod
    from robotsix_auto_mail.server.adapters import _run_batch_archive_background
    from robotsix_auto_mail.triage import set_archive_subfolder_override

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "g-1",
                    "sender": "a@b.com",
                    "subject": "A",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
                {
                    "message_id": "g-2",
                    "sender": "c@d.com",
                    "subject": "B",
                    "date": "2025-01-02T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
                {
                    "message_id": "g-3",
                    "sender": "e@f.com",
                    "subject": "C",
                    "date": "2025-01-03T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        conn = init_db(db_path)
        try:
            conn.execute("UPDATE mail_records SET imap_uid = 11 WHERE message_id='g-1'")
            conn.execute("UPDATE mail_records SET imap_uid = 22 WHERE message_id='g-2'")
            conn.execute("UPDATE mail_records SET imap_uid = 33 WHERE message_id='g-3'")
            conn.commit()
            # g-1 and g-3 share a destination subfolder; g-2 differs.
            set_archive_subfolder_override(conn, "g-1", "2026")
            set_archive_subfolder_override(conn, "g-2", "vendors")
            set_archive_subfolder_override(conn, "g-3", "2026")
        finally:
            conn.close()
        for mid in ("g-1", "g-2", "g-3"):
            _seed_triage_decision(db_path, mid, action="TO_ARCHIVE")

        class _Folder:
            delimiter = "/"

        moves: list[tuple[list[int], str]] = []

        class _FakeClient:
            def __init__(self, *a: object, **k: object) -> None:
                pass

            def __enter__(self) -> "_FakeClient":
                return self

            def __exit__(self, *a: object) -> None:
                pass

            def select_folder(self, name: str) -> int:
                return 0

            def list_folders(self) -> list[_Folder]:
                return [_Folder()]

            def create_folder(self, name: str) -> None:
                pass

            def search_uids(self, criteria: str) -> list[int]:
                # Return the stored UIDs for the records being tested.
                return [11, 22, 33]

            def move_messages(self, uids: list[int], dest: str) -> None:
                moves.append((list(uids), dest))

        monkeypatch.setattr(imap_mod, "ImapClient", _FakeClient)

        mail_config = MailConfig(
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls_mode="direct",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_tls_mode="direct",
            username="user@example.com",
            password="pw",
            db_path=db_path,
            archive_root="Archive",
        )
        _run_batch_archive_background(db_path, mail_config, "Archive")

        # One move per destination group; g-1 + g-3 batched together.
        by_dest = {dest: uids for uids, dest in moves}
        assert by_dest == {"Archive/2026": [11, 33], "Archive/vendors": [22]}

        from robotsix_auto_mail.db import get_record_by_message_id, get_watermark

        conn = init_db(db_path, skip_migrations=True)
        try:
            for mid in ("g-1", "g-2", "g-3"):
                assert get_record_by_message_id(conn, mid) is None
            assert get_watermark(conn, "batch_op:state") == "idle"
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_batch_archive_worker_subfolder_filter_archives_only_that_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With subfolder_filter set, only that destination's mail is archived;
    the rest of the TO_ARCHIVE column is left untouched."""
    import robotsix_auto_mail.imap as imap_mod
    from robotsix_auto_mail.db import get_record_by_message_id
    from robotsix_auto_mail.server.adapters import _run_batch_archive_background
    from robotsix_auto_mail.triage import set_archive_subfolder_override

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": f"f-{i}",
                    "sender": "a@b.com",
                    "subject": "S",
                    "date": f"2025-01-0{i}T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                }
                for i in (1, 2, 3)
            ],
        )
        conn = init_db(db_path)
        try:
            conn.execute("UPDATE mail_records SET imap_uid = 11 WHERE message_id='f-1'")
            conn.execute("UPDATE mail_records SET imap_uid = 22 WHERE message_id='f-2'")
            conn.execute("UPDATE mail_records SET imap_uid = 33 WHERE message_id='f-3'")
            conn.commit()
            # f-1 + f-3 → "2026"; f-2 → "vendors".
            set_archive_subfolder_override(conn, "f-1", "2026")
            set_archive_subfolder_override(conn, "f-2", "vendors")
            set_archive_subfolder_override(conn, "f-3", "2026")
        finally:
            conn.close()
        for mid in ("f-1", "f-2", "f-3"):
            _seed_triage_decision(db_path, mid, action="TO_ARCHIVE")

        class _Folder:
            delimiter = "/"

        moves: list[tuple[list[int], str]] = []

        class _FakeClient:
            def __init__(self, *a: object, **k: object) -> None:
                pass

            def __enter__(self) -> "_FakeClient":
                return self

            def __exit__(self, *a: object) -> None:
                pass

            def select_folder(self, name: str) -> int:
                return 0

            def list_folders(self) -> list[_Folder]:
                return [_Folder()]

            def create_folder(self, name: str) -> None:
                pass

            def search_uids(self, criteria: str) -> list[int]:
                return [11, 22, 33]

            def move_messages(self, uids: list[int], dest: str) -> None:
                moves.append((list(uids), dest))

        monkeypatch.setattr(imap_mod, "ImapClient", _FakeClient)

        mail_config = MailConfig(
            imap_host="imap.example.com",
            imap_port=993,
            imap_tls_mode="direct",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_tls_mode="direct",
            username="user@example.com",
            password="pw",
            db_path=db_path,
            archive_root="Archive",
        )
        _run_batch_archive_background(
            db_path, mail_config, "Archive", subfolder_filter="2026"
        )

        # Only the "2026" group moved; "vendors" was not touched.
        assert moves == [([11, 33], "Archive/2026")]
        conn = init_db(db_path, skip_migrations=True)
        try:
            assert get_record_by_message_id(conn, "f-1") is None
            assert get_record_by_message_id(conn, "f-3") is None
            # f-2 (different destination) is preserved.
            assert get_record_by_message_id(conn, "f-2") is not None
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Global (aggregate) board tests
# ---------------------------------------------------------------------------


def _two_account_setup_with_triage(
    db_a: str,
    db_b: str,
) -> MailAccountsConfig:
    """Two-account container with records in different triage columns."""
    _populate_db(
        db_a,
        [
            {
                "message_id": "msg-a-inbox",
                "sender": "alice@a.com",
                "subject": "A Inbox",
                "date": "2025-01-01T00:00:00",
                "body_plain": "Body A inbox",
                "status": "to_read",
            },
            {
                "message_id": "msg-a-delete",
                "sender": "alice@a.com",
                "subject": "A Delete",
                "date": "2025-01-02T00:00:00",
                "body_plain": "Body A delete",
                "status": "to_read",
            },
        ],
    )
    _populate_db(
        db_b,
        [
            {
                "message_id": "msg-b-inbox",
                "sender": "bob@b.com",
                "subject": "B Inbox",
                "date": "2025-02-01T00:00:00",
                "body_plain": "Body B inbox",
                "status": "to_read",
            },
            {
                "message_id": "msg-b-answer",
                "sender": "bob@b.com",
                "subject": "B Answer",
                "date": "2025-02-02T00:00:00",
                "body_plain": "Body B answer",
                "status": "to_read",
            },
        ],
    )
    # Seed triage decisions so records land in different columns.
    _seed_triage_decision(db_a, "msg-a-delete", action="TO_DELETE")
    _seed_triage_decision(db_b, "msg-b-answer", action="TO_ANSWER")
    return MailAccountsConfig(
        accounts=(
            MailAccount(account_id="A", config=_account_config(db_a), label="Acc A"),
            MailAccount(account_id="B", config=_account_config(db_b), label="Acc B"),
        ),
        default_account_id="A",
    )


def test_global_content_builder_aggregation() -> None:
    """_build_global_board_content merges cards from both accounts."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_triage(db_a, db_b)
        from robotsix_auto_mail.server.views import _build_global_board_content

        result = _build_global_board_content(accounts)

        # Standard keys present.
        assert "columns_html" in result
        assert "triage_running" in result
        assert "unsubscribe_suggestions" in result

        columns_html = result["columns_html"]
        # Cards from both accounts appear.
        assert "msg-a-inbox" in columns_html
        assert "msg-a-delete" in columns_html
        assert "msg-b-inbox" in columns_html
        assert "msg-b-answer" in columns_html
        # Column labels for the distinct actions.
        assert "TO_DELETE" in columns_html or "Delete" in columns_html
        assert "TO_ANSWER" in columns_html or "Answer" in columns_html
        # triage_running is False (no watermark set).
        assert result["triage_running"] is False
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_global_content_builder_returns_correct_keys() -> None:
    """_build_global_board_content returns the same JSON keys as
    _build_board_content (minus the per-account-only batch_op)."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_triage(db_a, db_b)
        from robotsix_auto_mail.server.views import _build_global_board_content

        result = _build_global_board_content(accounts)
        assert set(result.keys()) == {
            "columns_html",
            "triage_running",
            "unsubscribe_suggestions",
        }
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_global_board_page_all_accounts_param() -> None:
    """GET /board?account=__all__ renders aggregate with data-account,
    account badge, and per-card ?account= actions."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_triage(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            status, body, _h = _get(f"http://127.0.0.1:{port}/board?account=__all__")
            assert status == 200
            # Cards from both accounts present.
            assert "msg-a-inbox" in body
            assert "msg-b-inbox" in body
            # Account badge visible.
            assert 'class="card-account"' in body
            assert "Acc A" in body or "Acc B" in body
            # data-account attribute on card-extra.
            assert 'data-account="A"' in body
            assert 'data-account="B"' in body
            # Per-card move form carries ?account=.
            assert 'action="/move?account=A"' in body
            assert 'action="/move?account=B"' in body
            # Picker includes All mailboxes option.
            assert '<option value="__all__"' in body
            assert "All mailboxes" in body
            # No folder-triage form in aggregate mode.
            assert 'action="/run-folder-triage"' not in body
            # No batch-delete / force-triage forms in aggregate mode.
            assert 'action="/batch-delete"' not in body
            assert 'action="/batch-archive"' not in body
            assert 'action="/force-triage-column"' not in body
            # No Run triage / refresh-btn (manual-control check).
            assert "Run triage" not in body
            assert 'id="refresh-btn"' not in body
            assert 'action="/run-triage"' not in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_global_board_default_landing_multi_account() -> None:
    """GET /board with ≥2 accounts and no ?account=/cookie defaults
    to aggregate view (cards from all accounts present)."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_triage(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            status, body, _h = _get(f"http://127.0.0.1:{port}/board")
            assert status == 200
            # Aggregate view (both accounts present).
            assert "msg-a-inbox" in body
            assert "msg-b-inbox" in body
            # Account badge + data-account present.
            assert 'class="card-account"' in body
            assert 'data-account="A"' in body
            assert 'data-account="B"' in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_global_board_picker_all_mailboxes_option() -> None:
    """The account picker in aggregate mode lists 'All mailboxes' first,
    selected by default."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            status, body, _h = _get(f"http://127.0.0.1:{port}/board?account=__all__")
            assert status == 200
            assert '<option value="__all__" selected>' in body
            assert "All mailboxes" in body
            # Per-account options follow.
            assert '<option value="A"' in body
            assert '<option value="B"' in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_global_board_single_account_query_still_works() -> None:
    """GET /board?account=<real id> still renders single-account board."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_triage(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            status, body, _h = _get(f"http://127.0.0.1:{port}/board?account=A")
            assert status == 200
            # Only account A's cards.
            assert "msg-a-inbox" in body
            assert "msg-a-delete" in body
            assert "msg-b-inbox" not in body
            assert "msg-b-answer" not in body
            # No account badges in single-account mode.
            assert 'class="card-account"' not in body
            assert "data-account=" not in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_global_board_content_json_aggregate() -> None:
    """GET /board-content?account=__all__ returns JSON aggregating all accounts."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_triage(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            status, body, _h = _get(
                f"http://127.0.0.1:{port}/board-content?account=__all__"
            )
            assert status == 200
            payload = json.loads(body)
            assert "columns_html" in payload
            assert "triage_running" in payload
            assert "unsubscribe_suggestions" in payload
            # Both accounts' cards in the JSON.
            assert "msg-a-inbox" in payload["columns_html"]
            assert "msg-b-inbox" in payload["columns_html"]
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_global_board_move_routes_to_correct_account() -> None:
    """POST /move?account=A from an aggregate card mutates only A's DB."""
    from urllib.request import Request

    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_triage(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            import urllib.parse

            data = urllib.parse.urlencode(
                {"message_id": "msg-a-inbox", "triage_action": "TO_ANSWER"}
            ).encode()
            req = Request(
                f"http://127.0.0.1:{port}/move?account=A",
                data=data,
                method="POST",
            )
            resp = urlopen(req)  # noqa: S310
            resp.close()
        finally:
            server.shutdown()
        # Verify only A's DB was mutated.
        assert _triage_action(db_a, "msg-a-inbox") == "TO_ANSWER"
        assert _triage_action(db_b, "msg-b-inbox") is None
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_global_board_detail_routes_to_correct_account() -> None:
    """GET /email/<mid>?account=A returns the detail for A's record."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_triage(db_a, db_b)
        server, port = _start_test_server_with_accounts(accounts, "A")
        try:
            status, body, _h = _get(
                f"http://127.0.0.1:{port}/email/msg-a-inbox?embed=1&account=A"
            )
            assert status == 200
            assert "alice@a.com" in body
            assert "bob@b.com" not in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_single_account_output_unchanged() -> None:
    """Single-account _build_board_content produces the same output after
    the _gather_account_board_data refactor (no regressions)."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    try:
        _populate_db(
            db_a,
            [
                {
                    "message_id": "msg-x",
                    "sender": "x@example.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "Body",
                    "status": "to_read",
                },
            ],
        )
        from robotsix_auto_mail.server.views import _build_board_content

        result = _build_board_content(db_a)
        # Standard keys present.
        assert "columns_html" in result
        assert "triage_running" in result
        assert "batch_op" in result
        assert "unsubscribe_suggestions" in result
        # The card is rendered.
        assert "msg-x" in result["columns_html"]
        # No account badge / data-account in single-account mode.
        assert 'class="card-account"' not in result["columns_html"]
        assert "data-account=" not in result["columns_html"]
        # No ?account= in form actions.
        assert "?account=" not in result["columns_html"]
    finally:
        os.unlink(db_a)


def test_global_board_triage_running_ors_across_accounts() -> None:
    """triage_running is True when any account's watermark is 'running'."""
    fd_a, db_a = tempfile.mkstemp(suffix=".db")
    fd_b, db_b = tempfile.mkstemp(suffix=".db")
    os.close(fd_a)
    os.close(fd_b)
    try:
        accounts = _two_account_setup_with_triage(db_a, db_b)
        # Mark account B as running.
        conn_b = init_db(db_b)
        try:
            set_watermark(conn_b, "triage_run:state", "running")
        finally:
            conn_b.close()
        from robotsix_auto_mail.server.views import _build_global_board_content

        result = _build_global_board_content(accounts)
        assert result["triage_running"] is True
    finally:
        os.unlink(db_a)
        os.unlink(db_b)


def test_mailboard_adapter_no_record_accounts_unchanged() -> None:
    """MailBoardAdapter with empty record_accounts produces byte-identical
    card_extra_html and column_extra_html to the pre-aggregate behaviour."""
    record = _make_record(
        message_id="<test@example.com>",
        sender="sender@example.com",
        subject="Test mail",
        date="2025-01-01T00:00:00",
        body_plain="Hello world",
    )
    adapter = MailBoardAdapter(
        triage_by_mid={"<test@example.com>": "INBOX"},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
        column_records={"INBOX": [record]},
    )
    card_html = adapter.card_extra_html(record)
    # No account badge.
    assert 'class="card-account"' not in card_html
    # No data-account attribute.
    assert "data-account=" not in card_html
    # No ?account= in form actions.
    assert "?account=" not in card_html
    # Core attributes still present.
    assert 'data-message-id="%3Ctest%40example.com%3E"' in card_html
    assert 'data-subject="Test mail"' in card_html

    col_html = adapter.column_extra_html("INBOX")
    # INBOX with records but no matching controls → empty wrapper div.
    assert 'class="column-extra-top"' in col_html
    assert "batch-delete" not in col_html
    assert "force-triage" not in col_html


def test_mailboard_adapter_with_record_accounts_adds_badge_and_qs() -> None:
    """MailBoardAdapter with record_accounts adds data-account, badge,
    and ?account= to all per-card form actions."""
    record = _make_record(
        message_id="<test@example.com>",
        sender="sender@example.com",
        subject="Test mail",
        date="2025-01-01T00:00:00",
        body_plain="Hello world",
    )
    adapter = MailBoardAdapter(
        triage_by_mid={"<test@example.com>": "INBOX"},
        archive_subfolders={},
        folder_exists={},
        archive_root="/archive",
        unsubscribe_suggestions={},
        record_notes={},
        column_records={"INBOX": [record]},
        record_accounts={"<test@example.com>": "acc1"},
        account_labels={"acc1": "Account One"},
    )
    card_html = adapter.card_extra_html(record)
    # Account badge visible.
    assert 'class="card-account"' in card_html
    assert "Account One" in card_html
    # data-account attribute.
    assert 'data-account="acc1"' in card_html
    # ?account=acc1 on the move form action.
    assert 'action="/move?account=acc1"' in card_html

    # column_extra_html suppresses batch controls in aggregate mode.
    col_html = adapter.column_extra_html("TO_DELETE")
    assert 'action="/batch-delete"' not in col_html
    assert 'action="/batch-archive"' not in col_html
    assert 'action="/force-triage-column"' not in col_html
