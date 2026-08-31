"""Shared helpers for action-mixin unit tests.

Provides ``_FakeHandler`` (a concrete ``_BoardActionMixin`` for direct
mixin testing), ``_ComposeDraftFakeHandler`` (extends ``_FakeHandler``
with ``_ComposeDraftMixin`` for compose-to-Drafts unit tests), and
``_SyncThread`` (a synchronous ``threading.Thread`` replacement for
deterministic background-worker tests).
"""

from __future__ import annotations

from typing import Any, Callable
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server._account_mixin import _AccountMixin
from robotsix_auto_mail.server._action_mixin import _BoardActionMixin
from robotsix_auto_mail.server._archive_action_mixin import _ArchiveActionMixin
from robotsix_auto_mail.server._batch_mixin import _BatchActionMixin
from robotsix_auto_mail.server._compose_draft_mixin import _ComposeDraftMixin
from robotsix_auto_mail.server._view_mixin import _BoardViewMixin


class _FakeHandler(_BoardViewMixin, _ArchiveActionMixin, _BoardActionMixin):
    """Concrete handler that wires the ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so mixin methods can be called directly."""

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = None
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()


class _ComposeDraftFakeHandler(_ComposeDraftMixin, _BoardActionMixin):
    """Concrete handler that wires ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so compose-draft methods can be called directly."""

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = None
        self._current_account_id = None
        self._aggregate = False
        self._account_cookie = None
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()


class _BatchFakeHandler(_BatchActionMixin, _BoardActionMixin):
    """Concrete handler mixing in batch + action mixins for direct testing.

    Wires every ``BoardHandlerProtocol`` attribute to a ``MagicMock``
    default so mixin methods can be called without a real HTTP server.
    """

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
    ) -> None:
        self.db_path: str = db_path
        self.mail_config: MailConfig | None = mail_config
        self.accounts: Any = None
        self._aggregate: bool = False
        self._current_account_id: str | None = None
        self._account_cookie: str | None = None
        self.headers: Any = mock.MagicMock()
        self.rfile: Any = mock.MagicMock()
        self._send_response: Any = mock.MagicMock()
        self._redirect: Any = mock.MagicMock()
        self._not_found: Any = mock.MagicMock()
        self._bad_request: Any = mock.MagicMock()
        self._serve_json: Any = mock.MagicMock()


class _SyncThread:
    """Drop-in replacement for ``threading.Thread`` that runs *target*
    synchronously inside ``start()``."""

    def __init__(
        self,
        group: object = None,
        target: Callable[..., None] | None = None,
        name: str | None = None,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        *,
        daemon: bool | None = None,
    ) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}

    def start(self) -> None:
        if self._target is not None:
            self._target(*self._args, **self._kwargs)


# ---------------------------------------------------------------------------
# Account-mixin helpers
# ---------------------------------------------------------------------------


class _AccountMixinFakeHandler(_AccountMixin):
    """Concrete handler that wires ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so account-mixin methods can be called directly."""

    def __init__(
        self,
        db_path: str = "/tmp/test.db",  # noqa: S108
        mail_config: MailConfig | None = None,
        *,
        accounts: Any = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = accounts
        self._current_account_id = None
        self._aggregate = False
        self._account_cookie = None
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()
        self.server = mock.MagicMock()


_FORM_BODY = "account_id=test&imap_host=h&smtp_host=h&username=u&password=p"


def _make_post_body(**overrides: str) -> str:
    """Build a URL-encoded POST body from required defaults + overrides."""
    defaults = {
        "account_id": "test",
        "imap_host": "imap.example.com",
        "smtp_host": "smtp.example.com",
        "username": "user@example.com",
        "password": "secret",
    }
    defaults.update(overrides)
    return "&".join(f"{k}={v}" for k, v in defaults.items())
