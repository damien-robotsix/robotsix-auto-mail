"""Unit tests for ``_BoardViewMixin._serve_static``."""

from __future__ import annotations

from tests.server._view_mixin_helpers import _FakeHandler, tmp_db_path  # noqa: F401


class TestServeStatic:
    def test_board_js(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path, path="/static/board.js")
        handler._serve_static()
        handler._send_response.assert_called_once()
        _args, kwargs = handler._send_response.call_args
        assert kwargs["content_type"] == "text/javascript; charset=utf-8"

    def test_board_css(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path, path="/static/board.css")
        handler._serve_static()
        handler._send_response.assert_called_once()
        _args, kwargs = handler._send_response.call_args
        assert kwargs["content_type"] == "text/css; charset=utf-8"

    def test_automail_board_css(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path, path="/static/automail/board.css")
        handler._serve_static()
        handler._send_response.assert_called_once()
        _args, kwargs = handler._send_response.call_args
        assert kwargs["content_type"] == "text/css; charset=utf-8"

    def test_board_auto_mail_js(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path, path="/static/board-auto-mail.js")
        handler._serve_static()
        handler._send_response.assert_called_once()
        _args, kwargs = handler._send_response.call_args
        assert kwargs["content_type"] == "text/javascript; charset=utf-8"

    def test_unknown_path_returns_404(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path, path="/static/nonexistent.file")
        handler._serve_static()
        handler._not_found.assert_called_once()
        handler._send_response.assert_not_called()
