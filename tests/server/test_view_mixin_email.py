"""Unit tests for ``_BoardViewMixin._serve_email_status`` and ``_serve_email_detail``."""

from __future__ import annotations

from unittest import mock

import pytest

from tests.server._view_mixin_helpers import (
    _FakeHandler,
    mock_build_detail_html,  # noqa: F401
    mock_get_record_by_message_id,  # noqa: F401
    mock_get_triage_decision,  # noqa: F401
    mock_init_db,  # noqa: F401
    tmp_db_path,  # noqa: F401
)


class TestServeEmailStatus:
    @pytest.fixture(autouse=True)
    def patch_db(self, mock_init_db: mock.MagicMock) -> None:
        pass

    def test_returns_action_text(
        self,
        tmp_db_path: str,
        mock_init_db: mock.MagicMock,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_triage_decision: mock.MagicMock,
    ) -> None:
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_triage_decision.return_value = mock.MagicMock(action="TO_ARCHIVE")
        handler = _FakeHandler(tmp_db_path, path="/email/test%40example.com/status")
        handler._serve_email_status()
        handler._send_response.assert_called_once_with("TO_ARCHIVE")

    def test_inbox_fallback_when_no_decision(
        self,
        tmp_db_path: str,
        mock_init_db: mock.MagicMock,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_triage_decision: mock.MagicMock,
    ) -> None:
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_triage_decision.return_value = None
        handler = _FakeHandler(tmp_db_path, path="/email/test%40example.com/status")
        handler._serve_email_status()
        handler._send_response.assert_called_once_with("INBOX")

    def test_404_when_record_not_found(
        self,
        tmp_db_path: str,
        mock_init_db: mock.MagicMock,
        mock_get_record_by_message_id: mock.MagicMock,
    ) -> None:
        mock_get_record_by_message_id.return_value = None
        handler = _FakeHandler(tmp_db_path, path="/email/unknown%40example.com/status")
        handler._serve_email_status()
        handler._not_found.assert_called_once()
        handler._send_response.assert_not_called()


class TestServeEmailDetail:
    @pytest.fixture(autouse=True)
    def patch_build_detail(self, mock_build_detail_html: mock.MagicMock) -> None:
        pass

    def test_embed_mode_clears_account_cookie(
        self, tmp_db_path: str, mock_build_detail_html: mock.MagicMock
    ) -> None:
        mock_build_detail_html.return_value = "<html>detail</html>"
        handler = _FakeHandler(
            tmp_db_path,
            path="/email/test%40example.com?embed=1",
            _account_cookie="old-cookie",
        )
        handler._serve_email_detail()
        assert handler._account_cookie is None
        handler._send_response.assert_called_once_with(
            "<html>detail</html>", content_type="text/html; charset=utf-8"
        )

    def test_focus_draft_mode_passed_through(
        self, tmp_db_path: str, mock_build_detail_html: mock.MagicMock
    ) -> None:
        mock_build_detail_html.return_value = "<html>draft</html>"
        handler = _FakeHandler(tmp_db_path, path="/email/test%40example.com?draft=1")
        handler._serve_email_detail()
        mock_build_detail_html.assert_called_once_with(
            tmp_db_path,
            "test@example.com",
            embed=False,
            focus_draft=True,
            current_account_id=handler._current_account_id,
        )
        handler._send_response.assert_called_once_with(
            "<html>draft</html>", content_type="text/html; charset=utf-8"
        )

    def test_normal_mode_does_not_touch_account_cookie(
        self, tmp_db_path: str, mock_build_detail_html: mock.MagicMock
    ) -> None:
        mock_build_detail_html.return_value = "<html>detail</html>"
        handler = _FakeHandler(
            tmp_db_path,
            path="/email/test%40example.com",
            _account_cookie="keep-me",
        )
        handler._serve_email_detail()
        assert handler._account_cookie == "keep-me"

    def test_503_on_exception(
        self, tmp_db_path: str, mock_build_detail_html: mock.MagicMock
    ) -> None:
        mock_build_detail_html.side_effect = RuntimeError("boom")
        handler = _FakeHandler(tmp_db_path, path="/email/test%40example.com")
        handler._serve_email_detail()
        handler._send_response.assert_called_once_with(
            "Database unavailable", status=503
        )

    def test_404_when_detail_html_is_none(
        self, tmp_db_path: str, mock_build_detail_html: mock.MagicMock
    ) -> None:
        mock_build_detail_html.return_value = None
        handler = _FakeHandler(tmp_db_path, path="/email/test%40example.com")
        handler._serve_email_detail()
        handler._not_found.assert_called_once()
        handler._send_response.assert_not_called()
