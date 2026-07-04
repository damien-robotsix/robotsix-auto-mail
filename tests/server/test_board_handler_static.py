"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.request import urlopen

import pytest

if TYPE_CHECKING:
    pass

from tests.server.conftest import (
    _populate_db,
    _seed_triage_decision,
    _start_test_server,
)

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
        # Palette token and semantic variable for page background.
        assert "--palette-blue-950: #121626" in body
        assert "background: var(--color-bg-page)" in body
        # Palette token and semantic variable for the default button
        # background.
        assert "button {" in body
        assert "--palette-blue-800: #0f3460" in body
        assert "background: var(--color-bg-button)" in body
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
