"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.request import Request, urlopen

import pytest

if TYPE_CHECKING:
    pass

from robotsix_auto_mail.config import MailAccount, MailAccountsConfig
from tests.server.conftest import (
    _account_config,
    _get,
    _populate_db,
    _start_test_server,
    _start_test_server_with_accounts,
    _triage_action,
)

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
