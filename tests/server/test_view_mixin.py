"""Unit tests for ``_BoardViewMixin`` methods.

Drives the mixin directly against a mock handler *self*, isolating the
logic from the HTTP transport and covering branches that integration
tests miss (aggregate vs single-account branching, embed cookie
clearing, archive-folder empty-state, INBOX fallback, etc.).
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server._view_mixin import _BoardViewMixin

# ---------------------------------------------------------------------------
# Fake handler factory
# ---------------------------------------------------------------------------


class _FakeHandler(_BoardViewMixin):
    """Concrete handler that wires the ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so mixin methods can be called directly."""

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
        *,
        path: str = "/",
        _aggregate: bool = False,
        accounts: Any = None,
        _current_account_id: str | None = None,
        _account_cookie: str | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.path = path
        self._aggregate = _aggregate
        self.accounts = accounts
        self._current_account_id = _current_account_id
        self._account_cookie = _account_cookie
        self.default_account_id = None
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()


# ===================================================================
# _serve_static
# ===================================================================


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


# ===================================================================
# _serve_board
# ===================================================================


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


# ===================================================================
# _serve_board_content
# ===================================================================


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


# ===================================================================
# _serve_email_status
# ===================================================================


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


# ===================================================================
# _serve_email_detail
# ===================================================================


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
            calendar_enabled=False,
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


# ===================================================================
# _serve_archive_proposal
# ===================================================================


class TestServeArchiveProposal:
    @pytest.fixture(autouse=True)
    def _patch_imports(
        self,
        mock_init_db: mock.MagicMock,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        pass

    def test_404_when_record_not_found(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
    ) -> None:
        mock_get_record_by_message_id.return_value = None
        handler = _FakeHandler(tmp_db_path, path="/archive-proposal/test%40example.com")
        handler._serve_archive_proposal()
        handler._not_found.assert_called_once()

    def test_override_source(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mid = "override@example.com"
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_archive_subfolder.return_value = "Projects/Widgets"
        mock_load_archive_overrides.return_value = {mid: "Projects/Widgets"}
        mock_load_llm_archive_hints.return_value = {}
        mock_get_watermark.return_value = '{"delimiter":"/","folders":["Root","Root/Projects","Root/Projects/Widgets"]}'
        mock_parse_archive_structure.return_value = (
            {"Root", "Root/Projects", "Root/Projects/Widgets"},
            "/",
            "Root",
        )
        handler = _FakeHandler(
            tmp_db_path,
            path=f"/archive-proposal/{mid}",
        )
        handler._serve_archive_proposal()
        handler._serve_json.assert_called_once_with(
            {
                "subfolder": "Projects/Widgets",
                "archive_root": mock.ANY,
                "folder_exists": True,
                "overridden": True,
                "source": "override",
            }
        )

    def test_llm_source(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mid = "llm@example.com"
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_archive_subfolder.return_value = "Archive/2024"
        mock_load_archive_overrides.return_value = {}
        mock_load_llm_archive_hints.return_value = {mid: "Archive/2024"}
        mock_get_watermark.return_value = (
            '{"delimiter":"/","folders":["Root","Root/Archive","Root/Archive/2024"]}'
        )
        mock_parse_archive_structure.return_value = (
            {"Root", "Root/Archive", "Root/Archive/2024"},
            "/",
            "Root",
        )
        handler = _FakeHandler(
            tmp_db_path,
            path=f"/archive-proposal/{mid}",
        )
        handler._serve_archive_proposal()
        handler._serve_json.assert_called_once_with(
            {
                "subfolder": "Archive/2024",
                "archive_root": mock.ANY,
                "folder_exists": True,
                "overridden": False,
                "source": "llm",
            }
        )

    def test_rule_source(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mid = "rule@example.com"
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_archive_subfolder.return_value = "Misc"
        mock_load_archive_overrides.return_value = {}
        mock_load_llm_archive_hints.return_value = {}
        mock_get_watermark.return_value = (
            '{"delimiter":"/","folders":["Root","Root/Misc"]}'
        )
        mock_parse_archive_structure.return_value = (
            {"Root", "Root/Misc"},
            "/",
            "Root",
        )
        handler = _FakeHandler(
            tmp_db_path,
            path=f"/archive-proposal/{mid}",
        )
        handler._serve_archive_proposal()
        handler._serve_json.assert_called_once_with(
            {
                "subfolder": "Misc",
                "archive_root": mock.ANY,
                "folder_exists": True,
                "overridden": False,
                "source": "rule",
            }
        )

    def test_folder_exists_with_custom_delimiter(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mid = "rule@example.com"
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_archive_subfolder.return_value = "A/B/C"
        mock_load_archive_overrides.return_value = {}
        mock_load_llm_archive_hints.return_value = {}
        mock_get_watermark.return_value = (
            '{"delimiter":"/","folders":["Root","Root/A","Root/A/B","Root/A/B/C"]}'
        )
        mock_parse_archive_structure.return_value = (
            {"Root", "Root/A", "Root/A/B", "Root/A/B/C"},
            "/",
            "Root",
        )
        handler = _FakeHandler(
            tmp_db_path,
            path=f"/archive-proposal/{mid}",
        )
        handler._serve_archive_proposal()
        handler._serve_json.assert_called_once_with(
            {
                "subfolder": "A/B/C",
                "archive_root": mock.ANY,
                "folder_exists": True,
                "overridden": False,
                "source": "rule",
            }
        )

    def test_none_subfolder(
        self,
        tmp_db_path: str,
        mock_get_record_by_message_id: mock.MagicMock,
        mock_get_archive_subfolder: mock.MagicMock,
        mock_load_archive_overrides: mock.MagicMock,
        mock_load_llm_archive_hints: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mock_get_record_by_message_id.return_value = mock.MagicMock()
        mock_get_archive_subfolder.return_value = ""
        mock_load_archive_overrides.return_value = {}
        mock_load_llm_archive_hints.return_value = {}
        mock_get_watermark.return_value = '{"delimiter":"/","folders":["Root"]}'
        mock_parse_archive_structure.return_value = (
            {"Root"},
            "/",
            "Root",
        )
        handler = _FakeHandler(tmp_db_path, path="/archive-proposal/any@example.com")
        handler._serve_archive_proposal()
        call_args = handler._serve_json.call_args[0][0]
        assert call_args["subfolder"] == ""
        assert (
            call_args["folder_exists"] is True
        )  # full_path == effective_root == "Root"


# ===================================================================
# _serve_archive_folders
# ===================================================================


class TestServeArchiveFolders:
    @pytest.fixture(autouse=True)
    def _patch_imports(
        self,
        mock_init_db: mock.MagicMock,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        pass

    def test_aggregate_short_circuit(self, tmp_db_path: str) -> None:
        handler = _FakeHandler(tmp_db_path, _aggregate=True)
        handler._serve_archive_folders()
        handler._serve_json.assert_called_once_with({"delimiter": "/", "folders": []})

    def test_returns_sorted_subfolders(
        self,
        tmp_db_path: str,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mock_get_watermark.return_value = "ignored"
        mock_parse_archive_structure.return_value = (
            {"Root", "Root/2024", "Root/2023", "Root/2024/Q1"},
            "/",
            "Root",
        )
        handler = _FakeHandler(tmp_db_path, _aggregate=False)
        handler._serve_archive_folders()
        handler._serve_json.assert_called_once_with(
            {"delimiter": "/", "folders": ["2023", "2024", "2024/Q1"]}
        )

    def test_strips_effective_root_and_translates_delimiter(
        self,
        tmp_db_path: str,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mock_get_watermark.return_value = "ignored"
        mock_parse_archive_structure.return_value = (
            {"INBOX.Archive", "INBOX.Archive.2024", "INBOX.Archive.2024.Q1"},
            ".",
            "INBOX.Archive",
        )
        handler = _FakeHandler(tmp_db_path, _aggregate=False)
        handler._serve_archive_folders()
        handler._serve_json.assert_called_once_with(
            {"delimiter": "/", "folders": ["2024", "2024/Q1"]}
        )

    def test_empty_folders(
        self,
        tmp_db_path: str,
        mock_get_watermark: mock.MagicMock,
        mock_parse_archive_structure: mock.MagicMock,
    ) -> None:
        mock_get_watermark.return_value = "ignored"
        mock_parse_archive_structure.return_value = (
            {"Root"},
            "/",
            "Root",
        )
        handler = _FakeHandler(tmp_db_path, _aggregate=False)
        handler._serve_archive_folders()
        handler._serve_json.assert_called_once_with({"delimiter": "/", "folders": []})


# ===================================================================
# conftest fixtures for this module
# ===================================================================


@pytest.fixture(autouse=True)
def _patch_serve_board_deps() -> None:
    """Globally mock the board-rendering functions used by _serve_board
    and _serve_board_content so no real DB/rendering work occurs."""
    pass


@pytest.fixture
def mock_build_board_html() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_mixin._build_board_html",
        autospec=True,
    ) as m:
        m.return_value = "<html>board</html>"
        yield m


@pytest.fixture
def mock_build_global_board_html() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_mixin._build_global_board_html",
        autospec=True,
    ) as m:
        m.return_value = "<html>global board</html>"
        yield m


@pytest.fixture
def mock_build_board_content() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_mixin._build_board_content",
        autospec=True,
    ) as m:
        m.return_value = {"columns": {}}
        yield m


@pytest.fixture
def mock_build_global_board_content() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_mixin._build_global_board_content",
        autospec=True,
    ) as m:
        m.return_value = {"columns": {}}
        yield m


@pytest.fixture
def mock_build_detail_html() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_mixin._build_detail_html",
        autospec=True,
    ) as m:
        m.return_value = "<html>detail</html>"
        yield m


@pytest.fixture
def mock_init_db() -> "mock._patch":
    # init_db is imported locally inside _serve_archive_proposal,
    # _serve_archive_folders, and _serve_email_status — patch at source.
    with mock.patch(
        "robotsix_auto_mail.db.init_db",
        autospec=True,
    ) as m:
        yield m


@pytest.fixture
def mock_get_record_by_message_id() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.db.get_record_by_message_id",
        autospec=True,
    ) as m:
        yield m


@pytest.fixture
def mock_get_triage_decision() -> "mock._patch":
    # Imported at module level in _view_mixin.
    with mock.patch(
        "robotsix_auto_mail.server._view_mixin.get_triage_decision",
        autospec=True,
    ) as m:
        yield m


@pytest.fixture
def mock_get_archive_subfolder() -> "mock._patch":
    # Imported at module level in _view_mixin.
    with mock.patch(
        "robotsix_auto_mail.server._view_mixin.get_archive_subfolder",
        autospec=True,
    ) as m:
        m.return_value = "Inbox"
        yield m


@pytest.fixture
def mock_load_archive_overrides() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.triage._load_archive_overrides",
        autospec=True,
    ) as m:
        m.return_value = {}
        yield m


@pytest.fixture
def mock_load_llm_archive_hints() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.triage._load_llm_archive_hints",
        autospec=True,
    ) as m:
        m.return_value = {}
        yield m


@pytest.fixture
def mock_get_watermark() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.db.get_watermark",
        autospec=True,
    ) as m:
        m.return_value = None
        yield m


@pytest.fixture
def mock_parse_archive_structure() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_mixin._parse_archive_structure",
        autospec=True,
    ) as m:
        m.return_value = (set(), "/", "/archive")
        yield m


@pytest.fixture
def tmp_db_path() -> str:
    """A throwaway DB path string — no real file created."""
    return "test_view_mixin.db"
