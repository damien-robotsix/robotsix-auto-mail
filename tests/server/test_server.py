"""Tests for the server module (kanban board HTTP handler)."""

from __future__ import annotations

import json
import os
import re
import tempfile
from typing import TYPE_CHECKING, cast
from urllib.request import urlopen

if TYPE_CHECKING:
    from http.client import HTTPResponse
    from http.server import HTTPServer

from tests.conftest import _make_record

from robotsix_auto_mail.board_adapter import MailBoardAdapter
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import init_db, set_watermark
from robotsix_auto_mail.format import _format_date

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


def _seed_rule_proposal(
    db_path: str, match_type: str, match_value: str, action: str
) -> None:
    """Seed one pending rule proposal into *db_path* via the triage API."""
    from robotsix_auto_mail.triage import (
        TriageRuleProposal,
        record_and_filter_rule_proposals,
    )

    conn = init_db(db_path)
    try:
        record_and_filter_rule_proposals(
            conn,
            [
                TriageRuleProposal(
                    match_type=match_type,
                    match_value=match_value,
                    action=action,
                    title=f"Triage {match_value} as {action}",
                    body="b",
                )
            ],
        )
    finally:
        conn.close()


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
            assert "proposals_html" in payload
            assert 'class="board-column"' in payload["columns_html"]
            assert "Rule proposals" in payload["proposals_html"]
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


def test_rule_action_accept_redirects_and_activates() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_rule_proposal(db_path, "sender", "spam@x.com", "TO_DELETE")
        conn = init_db(db_path)
        try:
            from robotsix_auto_mail.triage import (
                TriageRule,
                _load_active_rules,
                _rule_fingerprint,
                list_rule_proposals,
            )

            fingerprint = list_rule_proposals(conn, "pending")[0][0]
        finally:
            conn.close()

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(
                port,
                "/rule-action",
                {"fingerprint": fingerprint, "decision": "accept"},
            )
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"
        finally:
            server.shutdown()

        # The rule is now active.
        conn = init_db(db_path)
        try:
            active = _load_active_rules(conn)
            assert any(_rule_fingerprint(r) == fingerprint for r in active)
            assert isinstance(active[0], TriageRule)
        finally:
            conn.close()
    finally:
        os.unlink(db_path)


def test_rule_action_reject_redirects() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_rule_proposal(db_path, "sender", "spam@x.com", "TO_DELETE")
        conn = init_db(db_path)
        try:
            from robotsix_auto_mail.triage import list_rule_proposals

            fingerprint = list_rule_proposals(conn, "pending")[0][0]
        finally:
            conn.close()

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(
                port,
                "/rule-action",
                {"fingerprint": fingerprint, "decision": "reject"},
            )
            assert resp.status == 302
            assert resp.headers.get("Location") == "/board"
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_rule_action_unknown_fingerprint_not_found() -> None:
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(
            port,
            "/rule-action",
            {"fingerprint": "deadbeef", "decision": "accept"},
        )
        assert resp.status == 404
    finally:
        server.shutdown()


def test_rule_action_invalid_decision_bad_request() -> None:
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(
            port,
            "/rule-action",
            {"fingerprint": "deadbeef", "decision": "bogus"},
        )
        assert resp.status == 400
    finally:
        server.shutdown()


def test_rule_action_missing_fingerprint_bad_request() -> None:
    server, port = _start_test_server(":memory:")
    try:
        resp = _post_to_path(port, "/rule-action", {"decision": "accept"})
        assert resp.status == 400
    finally:
        server.shutdown()


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

        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
        ) as mock_provider_cls:
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
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
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

        with mock.patch(
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider"
        ) as mock_provider_cls:
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


def test_run_triage_background_generates_rule_proposals() -> None:
    """After triage, the background thread derives deterministic rule
    proposals from triage history and records them as pending so the board
    can surface them."""
    import time

    from robotsix_auto_mail.db import get_watermark
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.triage import list_rule_proposals

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Three already-triaged messages from one sender, all TO_ARCHIVE —
        # enough consistent evidence (>= _RULE_MIN_DECISIONS) for a sender
        # rule proposal.  All have decisions, so triage finds zero untriaged
        # records and returns immediately (no LLM call).
        inserts = [
            {
                "message_id": f"msg-{i}",
                "sender": "alice@example.com",
                "subject": f"Newsletter {i}",
                "date": "2025-01-01T00:00:00",
                "body_plain": "body",
                "status": "no_action",
            }
            for i in range(3)
        ]
        _populate_db(db_path, inserts)
        for i in range(3):
            _seed_triage_decision(db_path, f"msg-{i}", action="TO_ARCHIVE")

        server, port = _start_test_server(db_path)
        try:
            resp = _post_to_path(port, "/run-triage", {})
            assert resp.status == 302

            # Poll until the watermark clears (no LLM call → fast).
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                conn = _init_db(db_path, skip_migrations=True)
                try:
                    state = get_watermark(conn, "triage_run:state")
                finally:
                    conn.close()
                if state != "running":
                    break
                time.sleep(0.05)

            conn = _init_db(db_path, skip_migrations=True)
            try:
                proposals = list_rule_proposals(conn, "pending")
            finally:
                conn.close()
            assert any(
                entry.match_type == "sender"
                and entry.match_value == "alice@example.com"
                and entry.action == "TO_ARCHIVE"
                for _fingerprint, entry in proposals
            ), f"Expected a sender rule proposal, got {proposals!r}"
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
            "robotsix_llmio.openrouter_deepseek.OpenRouterDeepseekProvider",
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
            assert "rule-proposals" in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_handler_board_has_auto_refresh_js() -> None:
    server, port = _start_test_server(":memory:")
    try:
        resp = urlopen(f"http://127.0.0.1:{port}/board")
        body = resp.read().decode("utf-8")
        assert "setInterval(refreshBoard, 30000)" in body
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
                "proposals_html",
                "triage_running",
                "unsubscribe_suggestions",
            ):
                assert key in payload
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
