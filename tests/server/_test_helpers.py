"""Shared helpers for action-mixin unit tests.

Provides ``_FakeHandler`` (a concrete ``_BoardActionMixin`` for direct
mixin testing), ``_DraftMixinFakeHandler`` (extends ``_FakeHandler`` with
``_DraftMixin`` for draft-handler unit tests), and ``_SyncThread`` (a
synchronous ``threading.Thread`` replacement for deterministic
background-worker tests).
"""

from __future__ import annotations

from typing import Any, Callable
from unittest import mock

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.server._action_mixin import _BoardActionMixin
from robotsix_auto_mail.server._draft_mixin import _DraftMixin


class _FakeHandler(_BoardActionMixin):
    """Concrete handler that wires the ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so mixin methods can be called directly."""

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()


class _DraftMixinFakeHandler(_DraftMixin, _BoardActionMixin):
    """Concrete handler that wires ``BoardHandlerProtocol`` attributes
    to MagicMock defaults so draft-mixin methods can be called directly."""

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
        self.default_account_id = None
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()


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
