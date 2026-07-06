"""Unit tests for ``_BoardActionMixin._handle_post_action``.

Covers missing-message-id (400), record-not-found (404),
action-returning-false skips redirect, safe redirect honoured,
unsafe redirect fallback, and empty redirect fallback.
"""

from __future__ import annotations

from unittest import mock

from tests.server._test_helpers import _FakeHandler
from tests.server.conftest_helpers import _populate_db


class TestHandlePostAction:
    def test_missing_message_id_returns_400(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 0
        handler.rfile.read.return_value = b""
        action = mock.MagicMock()

        handler._handle_post_action("message_id", "redirect_to", action=action)
        handler._bad_request.assert_called_once_with("Missing message_id")
        action.assert_not_called()

    def test_record_not_found_returns_404(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path)
        handler.headers.get.return_value = 50
        handler.rfile.read.return_value = b"message_id=does-not-exist&redirect_to=/foo"
        action = mock.MagicMock()

        handler._handle_post_action("message_id", "redirect_to", action=action)
        handler._not_found.assert_called_once()
        action.assert_not_called()

    def test_action_returns_false_skips_redirect(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "act-false",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 60
        handler.rfile.read.return_value = b"message_id=act-false&redirect_to=/safe"
        action = mock.MagicMock(return_value=False)

        handler._handle_post_action("message_id", "redirect_to", action=action)
        action.assert_called_once()
        handler._redirect.assert_not_called()

    def test_safe_redirect_to_used(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "safe-redir",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 70
        handler.rfile.read.return_value = (
            b"message_id=safe-redir&redirect_to=/some/board?col=1"
        )
        action = mock.MagicMock(return_value=True)

        handler._handle_post_action("message_id", "redirect_to", action=action)
        handler._redirect.assert_called_once_with("/some/board?col=1", code=302)

    def test_unsafe_redirect_to_falls_back_to_board(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "unsafe-redir",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 85
        handler.rfile.read.return_value = (
            b"message_id=unsafe-redir&redirect_to=//evil.com/phish"
        )
        action = mock.MagicMock(return_value=True)

        handler._handle_post_action("message_id", "redirect_to", action=action)
        handler._redirect.assert_called_once_with("/board", code=302)

    def test_empty_redirect_to_falls_back_to_board(self, single_db: str) -> None:
        _populate_db(
            single_db,
            [
                {
                    "message_id": "empty-redir",
                    "sender": "x@x.com",
                    "subject": "Test",
                    "date": "2025-01-01T00:00:00",
                    "body_plain": "body",
                    "status": "to_read",
                },
            ],
        )
        handler = _FakeHandler(single_db)
        handler.headers.get.return_value = 60
        handler.rfile.read.return_value = b"message_id=empty-redir&redirect_to="
        action = mock.MagicMock(return_value=True)

        handler._handle_post_action("message_id", "redirect_to", action=action)
        handler._redirect.assert_called_once_with("/board", code=302)
