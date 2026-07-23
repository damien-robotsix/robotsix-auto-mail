"""Unit tests for ``_BoardViewMixin._serve_board`` and ``_serve_board_content``."""

from __future__ import annotations

from unittest import mock

from tests.server._view_mixin_helpers import (
    _FakeHandler,
    _patch_serve_board_deps,  # noqa: F401
    mock_build_board_content,  # noqa: F401
    mock_build_board_html,  # noqa: F401
    mock_build_global_board_content,  # noqa: F401
    mock_build_global_board_html,  # noqa: F401
    tmp_db_path,  # noqa: F401
)


class TestServeBoard:
    def test_aggregate_success(
        self, tmp_db_path: str, mock_build_global_board_html: mock.MagicMock
    ) -> None:
        handler = _FakeHandler(tmp_db_path, _aggregate=True)
        handler.accounts = mock.MagicMock()
        handler._serve_board()
        handler._send_response.assert_called_once_with(
            mock_build_global_board_html.return_value,
            content_type="text/html; charset=utf-8",
        )

    def test_aggregate_503_on_exception(
        self, tmp_db_path: str, mock_build_global_board_html: mock.MagicMock
    ) -> None:
        mock_build_global_board_html.side_effect = RuntimeError("boom")
        handler = _FakeHandler(tmp_db_path, _aggregate=True)
        handler.accounts = mock.MagicMock()
        handler._serve_board()
        handler._send_response.assert_called_once_with(
            "Database unavailable", status=503
        )

    def test_single_account_success(
        self, tmp_db_path: str, mock_build_board_html: mock.MagicMock
    ) -> None:
        handler = _FakeHandler(tmp_db_path, _aggregate=False)
        handler._serve_board()
        handler._send_response.assert_called_once_with(
            mock_build_board_html.return_value,
            content_type="text/html; charset=utf-8",
        )

    def test_single_account_503_on_exception(
        self, tmp_db_path: str, mock_build_board_html: mock.MagicMock
    ) -> None:
        mock_build_board_html.side_effect = RuntimeError("boom")
        handler = _FakeHandler(tmp_db_path, _aggregate=False)
        handler._serve_board()
        handler._send_response.assert_called_once_with(
            "Database unavailable", status=503
        )


class TestServeBoardContent:
    def test_aggregate_success(
        self, tmp_db_path: str, mock_build_global_board_content: mock.MagicMock
    ) -> None:
        handler = _FakeHandler(tmp_db_path, _aggregate=True)
        handler.accounts = mock.MagicMock()
        handler._serve_board_content()
        handler._serve_json.assert_called_once_with(
            mock_build_global_board_content.return_value
        )

    def test_aggregate_503_on_exception(
        self, tmp_db_path: str, mock_build_global_board_content: mock.MagicMock
    ) -> None:
        mock_build_global_board_content.side_effect = RuntimeError("boom")
        handler = _FakeHandler(tmp_db_path, _aggregate=True)
        handler.accounts = mock.MagicMock()
        handler._serve_board_content()
        handler._serve_json.assert_called_once_with(
            {"error": "Database unavailable"}, status=503
        )

    def test_single_account_success(
        self, tmp_db_path: str, mock_build_board_content: mock.MagicMock
    ) -> None:
        handler = _FakeHandler(tmp_db_path, _aggregate=False)
        handler._serve_board_content()
        handler._serve_json.assert_called_once_with(
            mock_build_board_content.return_value
        )

    def test_single_account_503_on_exception(
        self, tmp_db_path: str, mock_build_board_content: mock.MagicMock
    ) -> None:
        mock_build_board_content.side_effect = RuntimeError("boom")
        handler = _FakeHandler(tmp_db_path, _aggregate=False)
        handler._serve_board_content()
        handler._serve_json.assert_called_once_with(
            {"error": "Database unavailable"}, status=503
        )
