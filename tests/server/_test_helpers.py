"""Shared helpers for server service unit tests.

Provides ``_RequestContextHelpers`` (the real ``_effective_archive_root`` /
``_require_imap_configured`` / ``_validate_archive_path`` guards mirrored from
``BoardHandler`` so stub request contexts get their genuine behaviour),
``_FakeHandler`` (a stub request *context* wiring the response sinks to
``MagicMock``s so services can be driven directly), ``_archive_service`` (a
factory that builds an :class:`ArchiveService` wired to a fake handler's
injected deps), ``_ActionServiceContext`` (a stub request context for the
composition-era ``ActionService`` tests), and ``_SyncThread`` (a synchronous
``threading.Thread`` replacement for deterministic background-worker tests).
"""

from __future__ import annotations

from typing import Any, Callable
from unittest import mock

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT, MailConfig
from robotsix_auto_mail.server._archive_service import ArchiveService


class _RequestContextHelpers:
    """Real ``_effective_archive_root`` / ``_require_imap_configured`` /
    ``_validate_archive_path`` helpers (mirroring ``BoardHandler``) for stub
    request contexts.

    These three guards used to live on ``_BoardViewMixin``; after the view
    migration they belong to ``BoardHandler`` itself.  Stub contexts mix this
    in so the real guard behaviour runs against their ``MagicMock`` response
    sinks.
    """

    mail_config: MailConfig | None
    _serve_json: Any
    _bad_request: Any

    @property
    def _effective_archive_root(self) -> str:
        return (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

    def _require_imap_configured(self) -> bool:
        if self.mail_config is None:
            self._serve_json(
                {"error": "IMAP not configured for this account"},
                status=503,
            )
            return False
        return True

    def _validate_archive_path(self, *folders: str) -> tuple[bool, str]:
        archive_root = self._effective_archive_root
        for folder in folders:
            if ".." in folder.split("/"):
                self._bad_request(f"'{folder}' escapes archive root")
                return False, ""
        return True, archive_root


class _FakeHandler(_RequestContextHelpers):
    """Stub request *context* that wires the ``RequestContext`` attributes
    to MagicMock defaults so composition-era services can be driven directly.

    Pair it with :func:`_archive_service` to exercise ``ArchiveService``
    methods against this fake context.
    """

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = None
        self._current_account_id: str | None = None
        self._aggregate = False
        self._account_cookie: str | None = None
        self.headers = mock.MagicMock()
        self.rfile = mock.MagicMock()
        self._send_response = mock.MagicMock()
        self._redirect = mock.MagicMock()
        self._not_found = mock.MagicMock()
        self._bad_request = mock.MagicMock()
        self._serve_json = mock.MagicMock()


def _archive_service(handler: _FakeHandler) -> ArchiveService:
    """Build an ``ArchiveService`` wired to *handler*'s injected deps."""
    return ArchiveService(
        db_path=handler.db_path,
        mail_config=handler.mail_config,
    )


class _ActionServiceContext:
    """Stub request context for ``ActionService`` unit tests.

    Wires every response sink to a ``MagicMock`` and exposes the per-request
    transport/state fields the shared ``handle_post_action`` skeleton reads,
    so the service can be driven directly without a real HTTP server.
    """

    def __init__(
        self,
        db_path: str,
        mail_config: MailConfig | None = None,
        *,
        accounts: Any = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = accounts
        self._current_account_id: str | None = None
        self._aggregate = False
        self._account_cookie: str | None = None
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


# ---------------------------------------------------------------------------
# Account-service helpers
# ---------------------------------------------------------------------------


class _AccountServiceContext:
    """Stub request context for ``AccountService`` unit tests.

    Wires every response sink to a ``MagicMock`` and exposes the per-request
    transport/state fields the service reads, so it can be driven directly
    without a real HTTP server."""

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
        self.path = ""
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
