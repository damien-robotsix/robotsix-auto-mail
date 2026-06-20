"""Tests for the board handler (HTTP request routing and board rendering)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.request import urlopen

if TYPE_CHECKING:
    pass

from tests.server.conftest import (
    _populate_db,
    _start_test_server,
)

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
