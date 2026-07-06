"""Shared test helper functions and classes for server integration tests.

These helpers were extracted from ``tests/server/conftest.py`` to keep the
conftest focused on ``@pytest.fixture`` definitions (~100 lines).
"""

from __future__ import annotations

import json
import os
import tempfile
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from http.client import HTTPResponse
    from http.server import HTTPServer

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig, MailConfig
from robotsix_auto_mail.db import init_db, set_watermark
from robotsix_auto_mail.server.board_adapter import MailBoardAdapter
from tests.conftest import _make_record


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """An opener handler that refuses to follow redirects."""

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
    """An opener handler that returns the error response body instead of raising."""

    def http_error_default(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: object,
        hdrs: object,
    ) -> object:
        return fp


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


def _build_opener(
    port: int, fields: dict[str, str], path: str
) -> tuple[urllib.request.OpenerDirector, urllib.request.Request]:
    """Build an opener and request for POSTing url-encoded *fields* to *path*."""
    data = urllib.parse.urlencode(fields).encode("utf-8")
    url = f"http://127.0.0.1:{port}{path}"

    opener = urllib.request.build_opener(NoRedirect(), CaptureError())
    req = urllib.request.Request(  # noqa: S310
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    return opener, req


def _post_form(
    port: int, fields: dict[str, str], path: str = "/move"
) -> tuple[int, str]:
    """POST url-encoded *fields* to *path* on the test server."""
    opener, req = _build_opener(port, fields, path)
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
    opener, req = _build_opener(port, fields, path)
    resp = opener.open(req)
    resp.read()
    return cast("HTTPResponse", resp)


def _post_to_path(port: int, path: str, fields: dict[str, str]) -> HTTPResponse:
    """POST url-encoded *fields* to *path* and return the raw response.

    Does not follow redirects and captures error responses so 302/400/404
    can be inspected directly.
    """
    opener, req = _build_opener(port, fields, path)
    resp = opener.open(req)
    resp.read()
    return cast("HTTPResponse", resp)


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


def _post_config_sync(port: int) -> tuple[int, str]:
    """POST an empty body to /config-sync; return (status, body)."""
    url = f"http://127.0.0.1:{port}/config-sync"

    opener = urllib.request.build_opener(CaptureError())
    req = urllib.request.Request(url, data=b"", method="POST")  # noqa: S310
    resp = opener.open(req)
    body = resp.read().decode("utf-8")
    return resp.status, body


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
    from urllib.request import Request, urlopen

    req = Request(url)  # noqa: S310
    if cookie is not None:
        req.add_header("Cookie", cookie)
    resp = urlopen(req)  # noqa: S310
    try:
        return resp.status, resp.read().decode("utf-8"), dict(resp.headers)
    finally:
        resp.close()


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


def _seed_batch_state(db_path: str, value: str) -> None:
    """Write the ``batch_op:state`` watermark directly."""
    conn = init_db(db_path)
    try:
        set_watermark(conn, "batch_op:state", value)
    finally:
        conn.close()


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
