"""Shared helpers for ``ViewService`` unit tests.

Provides ``_FakeHandler`` — a stub request *context* that wires the
``RequestContext`` attributes to ``MagicMock`` defaults and delegates each
``_serve_*`` call to a real :class:`~robotsix_auto_mail.server._view_service.ViewService`
instance — plus the conftest fixtures the four domain-focused view-service
test modules share.  The service reads all per-request state off the context,
so driving it through this stub exercises the real service body.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

import pytest

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server._view_service import ViewService
from tests.server._test_helpers import _RequestContextHelpers


class _FakeHandler(_RequestContextHelpers):
    """Stub request context whose ``BoardHandlerProtocol`` attributes are wired
    to MagicMock defaults, delegating each ``_serve_*`` method to a real
    ``ViewService`` so the service body runs against the stub context."""

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
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()
        self._service = ViewService(
            db_path=db_path,
            mail_config=mail_config,
            accounts=accounts,
        )

    # -- delegators: drive the real service with this stub as context ------

    def _serve_board(self) -> None:
        self._service.serve_board(self)

    def _serve_board_content(self) -> None:
        self._service.serve_board_content(self)

    def _serve_board_cards(self) -> None:
        self._service.serve_board_cards(self)

    def _serve_static(self) -> None:
        self._service.serve_static(self)

    def _serve_archive_proposal(self) -> None:
        self._service.serve_archive_proposal(self)

    def _serve_archive_folders(self) -> None:
        self._service.serve_archive_folders(self)

    def _serve_archive_messages(self, folder: str = "") -> None:
        self._service.serve_archive_messages(self, folder=folder)

    def _serve_email_status(self) -> None:
        self._service.serve_email_status(self)

    def _serve_email_detail(self) -> None:
        self._service.serve_email_detail(self)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_build_board_html() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_service._build_board_html",
        autospec=True,
    ) as m:
        m.return_value = "<html>board</html>"
        yield m


@pytest.fixture
def mock_build_global_board_html() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_service._build_global_board_html",
        autospec=True,
    ) as m:
        m.return_value = "<html>global board</html>"
        yield m


@pytest.fixture
def mock_build_board_content() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_service._build_board_content",
        autospec=True,
    ) as m:
        m.return_value = {"columns": {}}
        yield m


@pytest.fixture
def mock_build_global_board_content() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_service._build_global_board_content",
        autospec=True,
    ) as m:
        m.return_value = {"columns": {}}
        yield m


@pytest.fixture
def mock_build_detail_html() -> "mock._patch":
    with mock.patch(
        "robotsix_auto_mail.server._view_service._build_detail_html",
        autospec=True,
    ) as m:
        m.return_value = "<html>detail</html>"
        yield m


@pytest.fixture
def mock_init_db() -> "mock._patch":
    # init_db is called by ``_with_db`` (imported locally inside
    # _serve_archive_proposal, _serve_archive_folders, and
    # _serve_email_status) — patch at source.
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
    # Imported at module level in _view_service.
    with mock.patch(
        "robotsix_auto_mail.server._view_service.get_triage_decision",
        autospec=True,
    ) as m:
        yield m


@pytest.fixture
def mock_get_archive_subfolder() -> "mock._patch":
    # Imported at module level in _view_service.
    with mock.patch(
        "robotsix_auto_mail.server._view_service.get_archive_subfolder",
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
        "robotsix_auto_mail.server._view_service._parse_archive_structure",
        autospec=True,
    ) as m:
        m.return_value = (set(), "/", "/archive")
        yield m


@pytest.fixture
def fake_db_path() -> str:
    """A throwaway DB path string — no real file created."""
    return "test_view_mixin.db"
