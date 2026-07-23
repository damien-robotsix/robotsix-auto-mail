"""Shared helpers for ``_BoardViewMixin`` unit tests.

Provides ``_FakeHandler`` (a concrete ``_BoardViewMixin`` for direct
mixin testing) and the conftest fixtures that the four domain-focused
view-mixin test modules share.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server._view_mixin import _BoardViewMixin


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


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


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
        "robotsix_auto_mail.server._constants.init_db",
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
