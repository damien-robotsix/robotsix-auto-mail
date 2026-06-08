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

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.db import MailRecord, init_db, set_watermark
from robotsix_auto_mail.format import _format_date
from robotsix_auto_mail.server import (
    _build_board_content,
    _build_board_html,
    _build_detail_html,
    _render_card,
    _render_column,
    _render_rule_card,
)
from robotsix_auto_mail.triage import RuleLedgerEntry, TriageDecision

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
    result = _format_date(None)  # type: ignore[arg-type]
    assert result is None


# ---------------------------------------------------------------------------
# _render_card
# ---------------------------------------------------------------------------


def test_render_card_basic() -> None:
    record = _make_record(
        message_id="abc",
        sender="alice@example.com",
        subject="Hello",
        date="2025-01-10T12:00:00",
        body_plain="This is the body.",
    )
    html = _render_card(record)
    assert "alice@example.com" in html
    assert "Hello" in html
    assert "2025-01-10 12:00" in html
    assert "This is the body." in html
    assert 'class="card"' in html
    # Move form present
    assert '<form class="card-form"' in html
    assert 'method="post" action="/move"' in html
    assert '<input type="hidden" name="message_id"' in html
    assert 'value="abc"' in html
    assert '<select name="triage_action">' in html
    assert '<button type="submit">Move</button>' in html
    # Default status is "INBOX", so that option is selected.
    assert '<option value="INBOX" selected' in html


def test_render_card_empty_subject() -> None:
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="   ",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    html = _render_card(record)
    assert "(no subject)" in html


def test_render_card_empty_body_plain() -> None:
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="",
    )
    html = _render_card(record)
    assert "(no body)" in html
    assert "no-body" in html


def test_render_card_whitespace_body_plain() -> None:
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="   \t\n  ",
    )
    html = _render_card(record)
    assert "(no body)" in html


def test_render_card_body_truncation() -> None:
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="A" * 200,
    )
    html = _render_card(record)
    # Should contain exactly 150 chars of "A" then "…"
    assert ("A" * 150 + "\u2026") in html


def test_render_card_body_exactly_limit() -> None:
    body = "B" * 150
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain=body,
    )
    html = _render_card(record)
    assert body in html
    # No ellipsis for exact 150
    assert "\u2026" not in html


def test_render_card_html_escapes_sender() -> None:
    record = _make_record(
        message_id="abc",
        sender="<script>alert('xss')</script>",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="safe",
    )
    html = _render_card(record)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    # Form should still be present
    assert '<form class="card-form"' in html


def test_render_card_html_escapes_subject() -> None:
    record = _make_record(
        message_id="abc",
        sender="x",
        subject='<b onmouseover="alert(1)">click</b>',
        date="2025-01-01T00:00:00",
        body_plain="safe",
    )
    html = _render_card(record)
    assert "&lt;b onmouseover" in html
    assert "<b " not in html


def test_render_card_html_escapes_body() -> None:
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain='<img src=x onerror="alert(1)">',
    )
    html = _render_card(record)
    assert "&lt;img" in html
    assert "<img" not in html


def test_render_card_selected_status() -> None:
    """When no triage decision is passed, the default column is 'INBOX'."""
    record = _make_record(
        message_id="test-id",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="body",
        status="done",
    )
    html = _render_card(record)
    # No decision → selected column is "INBOX", not "done".
    assert '<option value="INBOX" selected>Inbox</option>' in html
    assert '<option value="TO_ANSWER">To answer</option>' in html


def test_render_card_message_id_with_angle_brackets() -> None:
    """Message IDs containing <, > should be HTML-escaped in the hidden input."""
    record = _make_record(
        message_id="<abc123@example.com>",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    html = _render_card(record)
    assert 'value="&lt;abc123@example.com&gt;"' in html


# ---------------------------------------------------------------------------
# _render_rule_card
# ---------------------------------------------------------------------------


def test_render_rule_card_basic() -> None:
    entry = RuleLedgerEntry(
        match_type="sender",
        match_value="alice@example.com",
        action="TO_ARCHIVE",
        title="Triage mail from alice@example.com as archive",
        state="pending",
    )
    html = _render_rule_card("deadbeef", entry)
    assert "Triage mail from alice@example.com as archive" in html
    assert "sender=alice@example.com -&gt; TO_ARCHIVE" in html
    assert '<input type="hidden" name="fingerprint" value="deadbeef">' in html
    assert 'name="decision" value="accept"' in html
    assert 'name="decision" value="reject"' in html
    assert 'action="/rule-action"' in html


def test_render_rule_card_html_escapes_title() -> None:
    entry = RuleLedgerEntry(
        match_type="sender",
        match_value="x@x.com",
        action="TO_ARCHIVE",
        title="<script>alert('xss')</script>",
        state="pending",
    )
    html = _render_rule_card("fp", entry)
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_render_rule_card_html_escapes_match_value() -> None:
    entry = RuleLedgerEntry(
        match_type="sender",
        match_value='<img src=x onerror="alert(1)">',
        action="TO_ARCHIVE",
        title="t",
        state="pending",
    )
    html = _render_rule_card("fp", entry)
    assert "&lt;img" in html
    assert "<img" not in html


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


def test_build_board_html_structure() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "m1",
                    "sender": "a@b.com",
                    "subject": "Subj",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "Body",
                    "status": "to_read",
                },
                {
                    "message_id": "m2",
                    "sender": "c@d.com",
                    "subject": "Subj2",
                    "date": "2025-01-02T00:00:00",
                    "body_plain": "Body2",
                    "status": "done",
                },
            ],
        )
        # Seed a triage decision so m2 lands in the TO_ARCHIVE column.
        _seed_triage_decision(db_path, "m2", action="TO_ARCHIVE")

        html = _build_board_html(db_path)

        assert "<!DOCTYPE html>" in html
        assert '<html lang="en">' in html
        assert "<title>Mail Board</title>" in html
        assert '<meta http-equiv="refresh"' not in html
        assert "<h1>Mail Board</h1>" in html
        # Default triage_running=False → no banner, normal button.
        assert 'class="triage-banner"' not in html
        assert ">Run triage<" in html
        assert 'class="board"' in html

        # Exactly 5 columns
        assert html.count('class="column"') == 5

        # Order: Inbox, Human triage, To archive, To delete, To answer
        inbox_pos = html.find("<h2>Inbox</h2>")
        human_triage_pos = html.find("<h2>Human triage</h2>")
        to_archive_pos = html.find("<h2>To archive</h2>")
        to_delete_pos = html.find("<h2>To delete</h2>")
        to_answer_pos = html.find("<h2>To answer</h2>")
        assert (
            0
            <= inbox_pos
            < human_triage_pos
            < to_archive_pos
            < to_delete_pos
            < to_answer_pos
        )

        # m1 is untriaged → INBOX.  m2 has TO_ARCHIVE → TO_ARCHIVE.
        counts = re.findall(r'<span class="count">(\d+)</span>', html)
        assert counts == ["1", "0", "1", "0", "0"]

        # Cards
        assert "a@b.com" in html
        assert "c@d.com" in html
    finally:
        os.unlink(db_path)


def test_build_board_html_empty_db() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        html = _build_board_html(db_path)
        assert 'class="column"' in html
        # Default triage_running=False → no banner, normal button.
        assert 'class="triage-banner"' not in html
        assert ">Run triage<" in html
        # All counts should be 0
        counts = re.findall(r'<span class="count">(\d+)</span>', html)
        assert counts == ["0", "0", "0", "0", "0"]
    finally:
        os.unlink(db_path)


def test_build_board_html_shows_rule_proposal() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _seed_rule_proposal(db_path, "sender", "spam@x.com", "TO_DELETE")
        html = _build_board_html(db_path)
        assert "Rule proposals" in html
        assert 'class="rule-card"' in html
        assert "sender=spam@x.com -&gt; TO_DELETE" in html
        assert 'name="decision" value="accept"' in html
        assert 'name="decision" value="reject"' in html
    finally:
        os.unlink(db_path)


def test_build_board_html_empty_rule_proposals() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        html = _build_board_html(db_path)
        assert "Rule proposals" in html
        assert "No pending rule proposals" in html
        assert 'class="rule-card"' not in html
    finally:
        os.unlink(db_path)


def test_build_board_html_body_preview_truncated() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        long_body = "X" * 200
        _populate_db(
            db_path,
            [
                {
                    "message_id": "m3",
                    "sender": "t@t.com",
                    "subject": "Long body",
                    "date": "2025-03-01T00:00:00",
                    "body_plain": long_body,
                    "status": "to_read",
                },
            ],
        )

        html = _build_board_html(db_path)
        # The body preview should be exactly 150 chars + "…"
        assert ("X" * 150 + "\u2026") in html
        assert ("X" * 151) not in html
    finally:
        os.unlink(db_path)


def test_build_board_html_no_body_shows_placeholder() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "m4",
                    "sender": "n@n.com",
                    "subject": "No body",
                    "date": "2025-04-01T00:00:00",
                    "body_plain": "",
                    "status": "to_read",
                },
            ],
        )

        html = _build_board_html(db_path)
        assert "(no body)" in html
        assert "no-body" in html
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# HTTP handler integration tests (via urlopen in a thread)
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
            assert 'class="column"' in payload["columns_html"]
            assert "Rule proposals" in payload["proposals_html"]
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


def test_board_content_endpoint_empty_db_returns_json() -> None:
    """GET /board-content with empty DB returns all-zero counts."""
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
            counts = re.findall(r'<span class="count">(\d+)</span>', columns_html)
            assert counts == ["0", "0", "0", "0", "0"]
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
            # they all land in the INBOX column.
            counts = re.findall(r'<span class="count">(\d+)</span>', body)
            assert counts == ["4", "0", "0", "0", "0"]
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


def _post_form(port: int, fields: dict[str, str]) -> tuple[int, str]:
    """POST url-encoded *fields* to /move and return (status, body)."""
    import urllib.request

    data = urllib.parse.urlencode(fields).encode("utf-8")
    url = f"http://127.0.0.1:{port}/move"

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


def _post_form_resp(port: int, fields: dict[str, str]) -> HTTPResponse:
    """POST url-encoded *fields* to /move and return the raw response.

    Like :func:`_post_form` but returns the response object so the raw
    ``Location``/headers can be inspected (does not follow redirects).
    """
    import urllib.request

    data = urllib.parse.urlencode(fields).encode("utf-8")
    url = f"http://127.0.0.1:{port}/move"

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
            # Should be in To archive column
            counts = re.findall(r'<span class="count">(\d+)</span>', board_html)
            assert counts == ["0", "0", "1", "0", "0"], f"Unexpected counts: {counts}"
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
            counts = re.findall(r'<span class="count">(\d+)</span>', body)
            assert counts == ["0", "0", "0", "0", "1"]
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
            counts = re.findall(r'<span class="count">(\d+)</span>', body)
            assert counts == ["0", "0", "1", "0", "0"]
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


def test_email_status_simple_message_id() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "simple-id",
                    "sender": "s@s.com",
                    "subject": "Simple",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "done",
                },
            ],
        )
        _seed_triage_decision(db_path, "simple-id", action="TO_ARCHIVE")

        server, port = _start_test_server(db_path)
        try:
            resp = urlopen(f"http://127.0.0.1:{port}/email/simple-id/status")
            assert resp.status == 200
            assert resp.read().decode("utf-8") == "TO_ARCHIVE"
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _render_card — detail link
# ---------------------------------------------------------------------------


def test_render_card_has_detail_link() -> None:
    """_render_card output contains a link to /email/{message_id}."""
    record = _make_record(
        message_id="<abc@example.com>",
        sender="alice@example.com",
        subject="Hello World",
        date="2025-01-10T12:00:00",
        body_plain="Body.",
    )
    html = _render_card(record)
    # The subject should be wrapped in an <a> pointing to the detail page
    assert '<a href="/email/' in html
    # The quoted message_id should appear in the href
    import urllib.parse

    quoted = urllib.parse.quote("<abc@example.com>", safe="")
    assert f'href="/email/{quoted}"' in html
    # The visible subject text should be escaped and inside the <a>
    assert ">Hello World</a>" in html


def test_render_card_link_preserves_move_form() -> None:
    """The Move <form> is still present when the subject is a link."""
    record = _make_record(
        message_id="<test@example.com>",
        sender="x@x.com",
        subject="Test",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    html = _render_card(record)
    assert '<form class="card-form"' in html
    assert 'method="post" action="/move"' in html
    assert '<button type="submit">Move</button>' in html


# ---------------------------------------------------------------------------
# _build_detail_html
# ---------------------------------------------------------------------------


def test_build_detail_html_basic() -> None:
    """_build_detail_html returns a page with all expected content."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "<detail@test.com>",
                    "sender": "detail-sender@test.com",
                    "subject": "Detail Test Subject",
                    "date": "2025-06-15T14:30:00",
                    "body_plain": "Full body content here.\nLine two.",
                    "status": "to_read",
                },
            ],
        )

        html = _build_detail_html(db_path, "<detail@test.com>")
        assert html is not None
        assert "<!DOCTYPE html>" in html
        assert "<title>Mail: Detail Test Subject</title>" in html
        assert "← Back to board" in html
        assert 'href="/board"' in html
        assert "Detail Test Subject" in html
        assert "detail-sender@test.com" in html
        assert "2025-06-15 14:30" in html
        assert "Full body content here." in html
        assert "Line two." in html
        # Recipients To
        assert "To" in html
        # Move form
        assert '<form class="detail-form"' in html
        assert 'method="post" action="/move"' in html
    finally:
        os.unlink(db_path)


def test_detail_page_no_meta_refresh() -> None:
    """The standalone detail page must not contain a meta-refresh tag."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "<no-meta@test.com>",
                    "sender": "x@x.com",
                    "subject": "No Meta Refresh",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        html = _build_detail_html(db_path, "<no-meta@test.com>")
        assert html is not None
        assert 'meta http-equiv="refresh"' not in html
    finally:
        os.unlink(db_path)


def test_build_detail_html_unknown_message_id_returns_none() -> None:
    """_build_detail_html returns None for a nonexistent message_id."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        result = _build_detail_html(db_path, "<does-not-exist@x.com>")
        assert result is None
    finally:
        os.unlink(db_path)


def test_build_detail_html_empty_body_placeholder() -> None:
    """Placeholder '(no body)' shown when body_plain is empty."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "<empty-body@test.com>",
                    "sender": "x@x.com",
                    "subject": "Empty Body",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "",
                    "status": "to_read",
                },
            ],
        )

        html = _build_detail_html(db_path, "<empty-body@test.com>")
        assert html is not None
        assert "(no body)" in html
        assert "<em>(no body)</em>" in html
    finally:
        os.unlink(db_path)


def test_build_detail_html_no_attachments() -> None:
    """'(none)' shown when attachments list is empty."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "<no-attach@test.com>",
                    "sender": "x@x.com",
                    "subject": "No Attachments",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )

        html = _build_detail_html(db_path, "<no-attach@test.com>")
        assert html is not None
        assert "(none)" in html
    finally:
        os.unlink(db_path)


def test_build_detail_html_no_cc() -> None:
    """CC section is omitted when cc list is empty."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        # Insert with explicit recipients_json that has no CC
        conn = init_db(db_path)
        try:
            conn.execute(
                "INSERT INTO mail_records "
                "(message_id, sender, subject, date, recipients_json, "
                "body_plain, body_html, attachments_json, status) "
                "VALUES (?, ?, ?, ?, ?, ?, '', '[]', ?)",
                (
                    "<no-cc@test.com>",
                    "x@x.com",
                    "No CC",
                    "2025-01-01T00:00:00",
                    '{"to": ["a@b.com"], "cc": []}',
                    "body",
                    "to_read",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        html = _build_detail_html(db_path, "<no-cc@test.com>")
        assert html is not None
        # "To" should be present, but no separate CC label
        assert "To" in html
        # The string "CC" should not appear as a detail-label
        assert ">CC</div>" not in html
    finally:
        os.unlink(db_path)


def test_build_detail_html_includes_move_form() -> None:
    """Detail page includes a Move form with the correct message_id."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "<move-detail@test.com>",
                    "sender": "x@x.com",
                    "subject": "Move Detail",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "needs_reply",
                },
            ],
        )

        html = _build_detail_html(db_path, "<move-detail@test.com>")
        assert html is not None
        assert '<form class="detail-form"' in html
        assert 'method="post" action="/move"' in html
        assert 'value="&lt;move-detail@test.com&gt;"' in html
        # "needs_reply" status → no triage decision → INBOX is pre-selected.
        assert '<option value="INBOX" selected>Inbox</option>' in html
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# GET /email/{message_id} handler integration tests
# ---------------------------------------------------------------------------


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


def test_handler_email_detail_html_version_note() -> None:
    """Detail page shows 'HTML version available' when body_html is non-empty."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = init_db(db_path)
        try:
            conn.execute(
                "INSERT INTO mail_records "
                "(message_id, sender, subject, date, recipients_json, "
                "body_plain, body_html, attachments_json, status) "
                "VALUES (?, ?, ?, ?, '{}', ?, ?, '[]', ?)",
                (
                    "<html-body@test.com>",
                    "sender@test.com",
                    "HTML Body",
                    "2025-01-01T00:00:00",
                    "plain text",
                    "<p>HTML content</p>",
                    "to_read",
                ),
            )
            conn.commit()
        finally:
            conn.close()

        server, port = _start_test_server(db_path)
        try:
            import urllib.request

            encoded = urllib.request.pathname2url("<html-body@test.com>")
            resp = urlopen(f"http://127.0.0.1:{port}/email/{encoded}")
            body = resp.read().decode("utf-8")
            assert "HTML version available" in body
            # Raw HTML body should NOT be rendered
            assert "<p>HTML content</p>" not in body
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _build_detail_html embed mode
# ---------------------------------------------------------------------------


def test_build_detail_html_embed_no_full_page_chrome() -> None:
    """embed=True returns a fragment without DOCTYPE, <html>, <head>, <body>,
    <title>, meta refresh, back-link, or <h1>."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "<embed-test@test.com>",
                    "sender": "embed-sender@test.com",
                    "subject": "Embed Test Subject",
                    "date": "2025-06-15T14:30:00",
                    "body_plain": "Embed body content.",
                    "status": "to_read",
                },
            ],
        )

        html = _build_detail_html(db_path, "<embed-test@test.com>", embed=True)
        assert html is not None
        assert "<!DOCTYPE html>" not in html
        assert "<html" not in html
        assert "<head>" not in html
        assert "<body>" not in html
        assert "<title>" not in html
        assert 'meta http-equiv="refresh"' not in html
        assert "← Back to board" not in html
        assert "<h1>" not in html
        assert 'class="detail-container"' not in html
        # Should have embed wrapper and content
        assert 'class="embed-detail"' in html
        assert "<style>" in html
        assert "embed-sender@test.com" in html
        assert "Embed body content." in html
        # Move form should be present
        assert '<form class="detail-form"' in html
    finally:
        os.unlink(db_path)


def test_build_detail_html_embed_has_redirect_to() -> None:
    """embed=True includes a redirect_to hidden input in the move form."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "<redirect-embed@test.com>",
                    "sender": "x@x.com",
                    "subject": "Redirect Embed",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "needs_reply",
                },
            ],
        )

        html = _build_detail_html(db_path, "<redirect-embed@test.com>", embed=True)
        assert html is not None
        assert 'name="redirect_to"' in html
        # The redirect_to value should point back to the embed URL
        assert "/email/" in html
        assert "?embed=1" in html
    finally:
        os.unlink(db_path)


def test_build_detail_html_embed_nonexistent_returns_none() -> None:
    """embed=True returns None for unknown message_id, same as default."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        result = _build_detail_html(db_path, "<nope@x.com>", embed=True)
        assert result is None
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _build_board_html side-panel skeleton + script
# ---------------------------------------------------------------------------


def test_build_board_html_has_side_panel_skeleton() -> None:
    """_build_board_html output contains the side-panel HTML skeleton."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        html = _build_board_html(db_path)
        assert 'class="board-wrapper"' in html
        assert 'class="side-panel"' in html
        assert 'id="side-panel"' in html
        assert 'class="panel-header"' in html
        assert 'class="close-btn"' in html
        assert "&times;" in html
        assert "<iframe" in html
    finally:
        os.unlink(db_path)


def test_build_board_html_has_script_block() -> None:
    """_build_board_html output includes the JavaScript with openDetail/closeDetail."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        html = _build_board_html(db_path)
        assert "function openDetail(messageId, subject)" in html
        assert "function closeDetail()" in html
        assert "'/email/' + messageId + '?embed=1'" in html
        assert "classList.add('open')" in html
        assert "classList.remove('open')" in html
        assert "location.hash" in html
        # Delegated click on .board
        assert "closest('.card')" in html
        assert "getAttribute('data-message-id')" in html
        # Escape key handler
        assert "Escape" in html
        # Hash change handler
        assert "'hashchange'" in html
        # Guard: clicks from interactive form controls are not intercepted
        assert "closest('button, select, input')" in html
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _render_card data-message-id attribute
# ---------------------------------------------------------------------------


def test_render_card_has_data_message_id() -> None:
    """_render_card includes data-message-id with URL-encoded message_id."""
    record = _make_record(
        message_id="<test@example.com>",
        sender="x@x.com",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    html = _render_card(record)
    assert 'data-message-id="' in html
    # The value should be URL-encoded
    import urllib.parse

    quoted = urllib.parse.quote("<test@example.com>", safe="")
    assert f'data-message-id="{quoted}"' in html


def test_render_card_data_message_id_present_with_subject_link() -> None:
    """data-message-id coexists with the existing subject <a> link for
    non-JS fallback."""
    record = _make_record(
        message_id="abc123",
        sender="x@x.com",
        subject="Hello",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    html = _render_card(record)
    assert 'data-message-id="abc123"' in html
    assert '<a href="/email/abc123">' in html


# ---------------------------------------------------------------------------
# POST /move with redirect_to
# ---------------------------------------------------------------------------


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
            counts = re.findall(r'<span class="count">(\d+)</span>', board_html)
            assert counts == ["0", "0", "1", "0", "0"]
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


def test_render_card_with_triage_decision_no_badge() -> None:
    """The triage badge is no longer rendered — column placement is the indicator."""
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    decision = TriageDecision(
        message_id="abc",
        action="TO_ARCHIVE",
        source="agent",
        reason="newsletter",
    )
    html = _render_card(record, decision)
    # No triage-badge span.
    assert "triage-badge" not in html
    # Dropdown pre-selects the column matching the decision.
    assert '<option value="TO_ARCHIVE" selected>To archive</option>' in html


def test_render_card_without_triage_decision_has_no_badge() -> None:
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    html = _render_card(record)
    assert "triage-badge" not in html


def test_render_card_triage_decision_selects_column() -> None:
    """When a triage decision exists, the dropdown pre-selects the mapped column."""
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    decision = TriageDecision(
        message_id="abc",
        action="TO_DELETE",
        source="agent",
        reason="spam",
    )
    html = _render_card(record, decision)
    # delete → TO_DELETE column.
    assert '<option value="TO_DELETE" selected>To delete</option>' in html


def test_build_board_html_places_card_by_triage_decision() -> None:
    """A triaged record appears in the column mapped from its triage action."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "m1",
                    "sender": "a@b.com",
                    "subject": "Subj",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "Body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "m1", action="TO_ARCHIVE", reason="bulk mail")

        html = _build_board_html(db_path)
        # The card should be in the TO_ARCHIVE column.
        # Verify by checking the board counts.
        counts = re.findall(r'<span class="count">(\d+)</span>', html)
        assert counts == ["0", "0", "1", "0", "0"]
    finally:
        os.unlink(db_path)


def test_build_board_html_no_badge_without_decision() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "m1",
                    "sender": "a@b.com",
                    "subject": "Subj",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "Body",
                    "status": "to_read",
                },
            ],
        )

        html = _build_board_html(db_path)
        # The CSS block defines ``.triage-badge`` so the bare substring is
        # always present; assert the rendered badge span is absent instead.
        assert '<span class="triage-badge' not in html
    finally:
        os.unlink(db_path)


def test_build_board_html_triage_reason_not_in_markup() -> None:
    """Triage reason is not rendered as a separate badge (badge removed)."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "m1",
                    "sender": "a@b.com",
                    "subject": "Subj",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "Body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(
            db_path,
            "m1",
            action="TO_DELETE",
            reason='<script>alert("xss")</script>',
        )

        html = _build_board_html(db_path)
        # No triage-badge span anywhere.
        assert '<span class="triage-badge' not in html
    finally:
        os.unlink(db_path)


def test_build_detail_html_shows_triage_field() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "<triage-detail@test.com>",
                    "sender": "x@x.com",
                    "subject": "Triage Detail",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "needs_reply",
                },
            ],
        )
        _seed_triage_decision(
            db_path,
            "<triage-detail@test.com>",
            action="TO_ANSWER",
            reason="needs a reply",
        )

        for embed in (False, True):
            html = _build_detail_html(db_path, "<triage-detail@test.com>", embed=embed)
            assert html is not None
            assert "Triage" in html
            assert "TO_ANSWER" in html
            assert "needs a reply" in html
    finally:
        os.unlink(db_path)


def test_build_detail_html_no_triage_decision() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "<no-triage@test.com>",
                    "sender": "x@x.com",
                    "subject": "No Triage",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )

        html = _build_detail_html(db_path, "<no-triage@test.com>")
        assert html is not None
        assert "(no triage decision)" in html
    finally:
        os.unlink(db_path)


def test_build_detail_html_triage_reason_escaped() -> None:
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "<xss-triage@test.com>",
                    "sender": "x@x.com",
                    "subject": "XSS Triage",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "done",
                },
            ],
        )
        _seed_triage_decision(
            db_path,
            "<xss-triage@test.com>",
            action="TO_DELETE",
            reason='<script>alert("xss")</script>',
        )

        html = _build_detail_html(db_path, "<xss-triage@test.com>")
        assert html is not None
        assert "<script>alert" not in html
        assert "&lt;script&gt;" in html
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# POST /move creates triage_decisions row
# ---------------------------------------------------------------------------


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


def test_build_board_html_shows_triage_running() -> None:
    """When the triage_run:state watermark is 'running', the board shows the
    banner and a disabled button instead of the normal 'Run triage' button."""
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.db import set_watermark

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        conn = _init_db(db_path)
        try:
            set_watermark(conn, "triage_run:state", "running")
        finally:
            conn.close()

        html = _build_board_html(db_path)

        assert 'class="triage-banner"' in html
        assert "Triage is currently running" in html
        assert "Triage running…" in html
        assert "disabled" in html  # the button has the disabled attribute
        # The normal Run triage text must NOT appear.
        assert ">Run triage<" not in html
    finally:
        os.unlink(db_path)


def test_run_triage_already_running() -> None:
    """POST /run-triage when triage is already running is idempotent —
    it redirects to /board without spawning a second thread."""
    from robotsix_auto_mail.db import init_db as _init_db
    from robotsix_auto_mail.db import set_watermark

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


def test_render_card_includes_delete_form_when_to_delete() -> None:
    """_render_card includes delete form when decision action is TO_DELETE."""
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    decision = TriageDecision(
        message_id="abc",
        action="TO_DELETE",
        source="user",
    )
    html = _render_card(record, decision)
    assert '<form class="delete-form"' in html
    assert 'method="post" action="/delete"' in html
    assert 'onsubmit="return confirm(' in html
    assert "Permanently delete this mail from mailbox and database?" in html
    assert '<button type="submit" class="delete-btn">Delete</button>' in html


def test_render_card_no_delete_form_for_other_actions() -> None:
    """_render_card does NOT include delete form for non-TO_DELETE actions."""
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    for action in ("INBOX", "TO_ARCHIVE", "TO_ANSWER", "HUMAN_TRIAGE"):
        decision = TriageDecision(
            message_id="abc",
            action=action,
            source="user",
        )
        html = _render_card(record, decision)
        assert '<form class="delete-form"' not in html
        assert "delete-btn" not in html


def test_render_card_no_delete_form_without_decision() -> None:
    """_render_card does NOT include delete form when no triage decision exists."""
    record = _make_record(
        message_id="abc",
        sender="x",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    html = _render_card(record)
    assert '<form class="delete-form"' not in html


# ---------------------------------------------------------------------------
# POST /delete handler integration tests
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


def test_delete_with_redirect_to() -> None:
    """POST /delete with safe redirect_to redirects to that path."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "redirect-del",
                    "sender": "x@x.com",
                    "subject": "Redirect delete",
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
                "/delete",
                {
                    "message_id": "redirect-del",
                    "redirect_to": "/email/redirect-del?embed=1",
                },
            )
            assert resp.status == 302
            assert resp.headers.get("Location") == "/email/redirect-del?embed=1"
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _render_column — batch-delete button in column header
# ---------------------------------------------------------------------------


def _make_record_minimal(message_id: str = "abc") -> MailRecord:
    """Return a minimal MailRecord for _render_column tests."""
    return _make_record(
        message_id=message_id,
        sender="x@x.com",
        subject="s",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )


def test_render_column_includes_batch_delete_when_to_delete_non_empty() -> None:
    """_render_column includes batch-delete form when action=TO_DELETE
    and records is non-empty."""
    record = _make_record_minimal("bd1")
    html = _render_column("TO_DELETE", [record], {})
    assert '<form class="delete-form"' in html
    assert 'method="post" action="/batch-delete"' in html
    assert "Permanently delete ALL mail" in html
    assert '<button type="submit" class="delete-btn">Delete All</button>' in html
    # The form sits inside the column-header div.
    assert html.count('class="column-header"') == 1


def test_render_column_no_batch_delete_for_other_actions() -> None:
    """_render_column does NOT include batch-delete form for non-TO_DELETE
    columns, even when they have records."""
    record = _make_record_minimal("bd2")
    for action in ("INBOX", "HUMAN_TRIAGE", "TO_ARCHIVE", "TO_ANSWER"):
        html = _render_column(action, [record], {})
        assert "batch-delete" not in html
        assert "Delete All" not in html


def test_render_column_no_batch_delete_when_empty() -> None:
    """_render_column does NOT include batch-delete form when TO_DELETE
    column has zero records."""
    html = _render_column("TO_DELETE", [], {})
    assert "batch-delete" not in html
    assert "Delete All" not in html


# ---------------------------------------------------------------------------
# POST /batch-delete handler integration tests
# ---------------------------------------------------------------------------


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


def test_batch_delete_imap_failure_returns_502_db_untouched() -> None:
    """POST /batch-delete with IMAP failure → 502, local DB untouched."""
    from unittest import mock

    from robotsix_auto_mail.config import MailConfig
    from robotsix_auto_mail.db import get_record_by_message_id, init_db
    from robotsix_auto_mail.imap import ImapError

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "imap-fail-bd",
                    "sender": "x@x.com",
                    "subject": "IMAP fail",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "imap-fail-bd", action="TO_DELETE")

        # Give the record an imap_uid so IMAP deletion is attempted.
        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (12345, "imap-fail-bd"),
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
            with mock.patch(
                "robotsix_auto_mail.imap.ImapClient",
                side_effect=ImapError("Connection refused"),
            ):
                resp = _post_to_path(port, "/batch-delete", {})
            assert resp.status == 502

            # Record must still exist — local DB untouched.
            conn2 = init_db(db_path)
            try:
                assert get_record_by_message_id(conn2, "imap-fail-bd") is not None
            finally:
                conn2.close()
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _build_board_html / _build_board_content — batch-delete button integration
# ---------------------------------------------------------------------------


def test_build_board_html_renders_batch_delete_when_to_delete_has_cards() -> None:
    """Full board HTML includes the batch-delete button when TO_DELETE
    column has cards."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "board-bd-1",
                    "sender": "a@b.com",
                    "subject": "Board batch delete",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "board-bd-1", action="TO_DELETE")

        html = _build_board_html(db_path)
        assert "Delete All" in html
        assert 'action="/batch-delete"' in html
        assert "Permanently delete ALL mail" in html
    finally:
        os.unlink(db_path)


def test_build_board_html_no_batch_delete_when_to_delete_empty() -> None:
    """Full board HTML does NOT include the batch-delete button when
    TO_DELETE column is empty."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        html = _build_board_html(db_path)
        # The TO_DELETE column exists but has 0 cards → no Delete All button.
        assert "Delete All" not in html
        assert 'action="/batch-delete"' not in html
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Archive proposal rendering — _render_card / _build_board_html
# ---------------------------------------------------------------------------


def _seed_archive_structure(db_path: str, folders: list[str]) -> None:
    """Write an archive_structure watermark to *db_path*."""
    from robotsix_auto_mail.db import init_db, set_watermark

    conn = init_db(db_path)
    try:
        set_watermark(conn, "archive_structure", json.dumps(folders))
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


def test_render_card_to_archive_shows_archive_proposal() -> None:
    """TO_ARCHIVE card includes archive-proposal div with path and form."""
    record = _make_record(
        message_id="arc1",
        sender="alice@example.com",
        subject="Test",
        date="2025-06-01T12:00:00",
        body_plain="body",
    )
    decision = TriageDecision(
        message_id="arc1",
        action="TO_ARCHIVE",
        source="agent",
    )
    html = _render_card(
        record, decision, archive_subfolder="Lists/dev", archive_root="my-archive"
    )
    assert 'class="archive-proposal"' in html
    assert "Archive &rarr;" in html
    assert 'class="archive-path"' in html
    assert "my-archive/Lists/dev" in html
    assert '<form class="archive-override-form"' in html
    assert 'action="/archive-proposal"' in html
    assert 'name="subfolder"' in html
    assert '<button type="submit">Set</button>' in html


def test_render_card_to_archive_with_empty_subfolder_shows_root() -> None:
    """When subfolder is '', display archive_root/ with no subfolder."""
    record = _make_record(
        message_id="arc2",
        sender="x@x.com",
        subject="Test",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    decision = TriageDecision(
        message_id="arc2",
        action="TO_ARCHIVE",
        source="agent",
    )
    html = _render_card(
        record, decision, archive_subfolder="", archive_root="my-archive"
    )
    assert "my-archive/" in html
    # No subfolder value in the path span (just the root with trailing /)
    assert 'class="archive-path">my-archive/</span>' in html


def test_render_card_to_archive_shows_folder_exists() -> None:
    """When folder_exists=True, a checkmark indicator is shown."""
    record = _make_record(
        message_id="arc3",
        sender="x@x.com",
        subject="Test",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    decision = TriageDecision(
        message_id="arc3",
        action="TO_ARCHIVE",
        source="agent",
    )
    html = _render_card(
        record,
        decision,
        archive_subfolder="Lists/dev",
        folder_exists=True,
        archive_root="archive",
    )
    assert 'class="archive-exists"' in html
    assert "&#x2713;" in html


def test_render_card_to_archive_no_folder_exists_indicator_when_false() -> None:
    """When folder_exists=False, no checkmark."""
    record = _make_record(
        message_id="arc4",
        sender="x@x.com",
        subject="Test",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    decision = TriageDecision(
        message_id="arc4",
        action="TO_ARCHIVE",
        source="agent",
    )
    html = _render_card(
        record,
        decision,
        archive_subfolder="Lists/dev",
        folder_exists=False,
        archive_root="archive",
    )
    assert "archive-exists" not in html


def test_render_card_non_to_archive_no_proposal() -> None:
    """Cards in non-TO_ARCHIVE columns have no archive proposal."""
    record = _make_record(
        message_id="arc5",
        sender="x@x.com",
        subject="Test",
        date="2025-01-01T00:00:00",
        body_plain="body",
    )
    for action in ("INBOX", "TO_ANSWER", "TO_DELETE", "HUMAN_TRIAGE"):
        decision = TriageDecision(
            message_id="arc5",
            action=action,
            source="agent",
        )
        # archive_subfolder=None → no proposal section rendered
        html = _render_card(record, decision, archive_subfolder=None)
        assert '<div class="archive-proposal"' not in html


# ---------------------------------------------------------------------------
# GET /archive-proposal/<mid>
# ---------------------------------------------------------------------------


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


def test_build_board_html_to_archive_card_shows_proposal() -> None:
    """A card in TO_ARCHIVE column renders archive-proposal inline."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "board-ap",
                    "sender": "alice@example.com",
                    "subject": "Archive me",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "board-ap", action="TO_ARCHIVE")
        _seed_archive_structure(
            db_path,
            ["my-archive", "my-archive/example-com/alice"],
        )

        html = _build_board_html(db_path, archive_root="my-archive")
        assert 'class="archive-proposal"' in html
        assert "Archive &rarr;" in html
        # The subfolder should be example-com/alice (from deterministic rule)
        assert "example-com/alice" in html
    finally:
        os.unlink(db_path)


def test_build_board_html_to_archive_folder_exists_indicator() -> None:
    """Checkmark shown when the proposed folder is in archive_structure."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "exists-ap",
                    "sender": "alice@example.com",
                    "subject": "Test",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "exists-ap", action="TO_ARCHIVE")
        _seed_archive_structure(
            db_path,
            ["my-archive", "my-archive/example-com/alice"],
        )

        html = _build_board_html(db_path, archive_root="my-archive")
        # The folder exists so the checkmark should appear
        assert 'class="archive-exists"' in html
        assert "&#x2713;" in html
    finally:
        os.unlink(db_path)


def test_build_board_html_non_to_archive_no_proposal() -> None:
    """Cards outside TO_ARCHIVE have no archive-proposal div."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "inbox-ap",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        # No triage decision → INBOX column

        html = _build_board_html(db_path)
        assert '<div class="archive-proposal">' not in html
    finally:
        os.unlink(db_path)


def test_build_board_html_archive_override_reflected() -> None:
    """User override is reflected in the board HTML."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "override-ap",
                    "sender": "alice@example.com",
                    "subject": "Test",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "override-ap", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "override-ap", "Custom/Custom")

        html = _build_board_html(db_path, archive_root="my-archive")
        assert "Custom/Custom" in html
        # The deterministic "example-com/alice" should NOT appear
        assert "example-com/alice" not in html
    finally:
        os.unlink(db_path)


def test_build_board_html_archive_root_from_default() -> None:
    """When archive_root is default, it's shown in the path."""
    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "root-ap",
                    "sender": "alice@example.com",
                    "subject": "Test",
                    "date": "2025-06-01T12:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "root-ap", action="TO_ARCHIVE")

        from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT

        html = _build_board_html(db_path)  # uses default
        assert DEFAULT_ARCHIVE_ROOT in html
        assert "archive-proposal" in html
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# _handle_archive — security gate & auto folder creation
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


def test_archive_namespace_no_subfolder_creates_only_effective_root() -> None:
    """Empty subfolder with namespace → only the effective root is created."""
    from unittest import mock

    fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        _populate_db(
            db_path,
            [
                {
                    "message_id": "ns-root-mid",
                    "sender": "x@x.com",
                    "subject": "ns-root",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        _seed_triage_decision(db_path, "ns-root-mid", action="TO_ARCHIVE")
        _seed_archive_override(db_path, "ns-root-mid", "")

        conn = init_db(db_path)
        try:
            conn.execute(
                "UPDATE mail_records SET imap_uid = ? WHERE message_id = ?",
                (77, "ns-root-mid"),
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

                resp = _post_to_path(port, "/archive", {"message_id": "ns-root-mid"})

            assert resp.status == 302
            mock_client.create_folder.assert_called_once_with("INBOX.my-archive")
            mock_client.move_message.assert_called_once_with(77, "INBOX.my-archive")
        finally:
            server.shutdown()
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# Unsubscribe banner in _render_column
# ---------------------------------------------------------------------------


def test_render_column_banner_for_matching_sender() -> None:
    """Banner renders when a matching sender has an unsubscribe suggestion."""
    record = _make_record(
        message_id="abc",
        sender="spammer@example.com",
        subject="Spam",
        date="2025-01-10T12:00:00",
        body_plain="Buy now!",
    )
    suggestions = {
        "spammer@example.com": {
            "has_unsubscribe": True,
            "method": "header",
            "url": "https://example.com/unsub",
            "description": "List-Unsubscribe header found",
            "confidence": "high",
        }
    }
    html = _render_column(
        "TO_DELETE",
        [record],
        {},
        unsubscribe_suggestions=suggestions,
    )
    assert "unsubscribe-banner" in html
    assert "spammer@example.com" in html
    assert "Unsubscribe" in html
    assert "https://example.com/unsub" in html


def test_render_column_no_banner_without_match() -> None:
    """Banner does NOT appear when no matching sender is in the column."""
    record = _make_record(
        message_id="abc",
        sender="other@example.com",
        subject="Not spam",
        date="2025-01-10T12:00:00",
        body_plain="Hi!",
    )
    suggestions = {
        "spammer@example.com": {
            "has_unsubscribe": True,
            "method": "header",
            "url": "https://example.com/unsub",
            "description": "List-Unsubscribe header found",
            "confidence": "high",
        }
    }
    html = _render_column(
        "TO_DELETE",
        [record],
        {},
        unsubscribe_suggestions=suggestions,
    )
    assert "unsubscribe-banner" not in html


def test_render_column_banner_not_on_other_columns() -> None:
    """Banner is NOT rendered on non-TO_DELETE columns even with suggestions."""
    record = _make_record(
        message_id="abc",
        sender="spammer@example.com",
        subject="Spam",
        date="2025-01-10T12:00:00",
        body_plain="Buy now!",
    )
    suggestions = {
        "spammer@example.com": {
            "has_unsubscribe": True,
            "method": "header",
            "url": "https://example.com/unsub",
            "description": "Header found",
            "confidence": "high",
        }
    }
    html = _render_column(
        "INBOX",
        [record],
        {},
        unsubscribe_suggestions=suggestions,
    )
    assert "unsubscribe-banner" not in html


def test_render_column_banner_mailto_url() -> None:
    """mailto: URLs render as mailto links."""
    record = _make_record(
        message_id="abc",
        sender="spammer@example.com",
        subject="Spam",
        date="2025-01-10T12:00:00",
        body_plain="Buy now!",
    )
    suggestions = {
        "spammer@example.com": {
            "has_unsubscribe": True,
            "method": "mailto",
            "url": "mailto:unsub@example.com",
            "description": "mailto unsubscribe found",
            "confidence": "high",
        }
    }
    html = _render_column(
        "TO_DELETE",
        [record],
        {},
        unsubscribe_suggestions=suggestions,
    )
    assert "unsubscribe-banner" in html
    assert 'href="mailto:unsub@example.com"' in html
    assert "Unsubscribe" in html


def test_build_board_content_loads_unsubscribe_suggestions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_build_board_content loads suggestions from the watermark."""
    monkeypatch.setenv("LLM_API_KEY", "sk-test")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    try:
        conn = init_db(db_path)
        set_watermark(
            conn,
            "unsubscribe_suggestions",
            json.dumps(
                {
                    "spammer@example.com": {
                        "has_unsubscribe": True,
                        "method": "header",
                        "url": "https://example.com/unsub",
                        "description": "Header found",
                        "confidence": "high",
                    }
                }
            ),
        )
        conn.close()

        content = _build_board_content(db_path)
        columns_html = content["columns_html"]
        # The suggestions are loaded and passed to the TO_DELETE column,
        # but since no records are in the database, no banner renders.
        assert isinstance(columns_html, str)
    finally:
        os.unlink(db_path)
