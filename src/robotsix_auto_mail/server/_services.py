"""Service container for the composition-era board server.

See :mod:`robotsix_auto_mail.server._request_helpers` for the full
migration recipe.  This module defines the two building blocks every
migrated endpoint group uses:

* :class:`Service` — base class for a *stateless* endpoint group.  It stores
  only the injected, request-independent dependencies (``db_path``,
  ``mail_config``, ``accounts``).  Per-request state (the resolved account,
  the aggregate flag, the transport) lives on the handler/context, never on a
  service, so a single service instance is safe to share across every
  request.
* :class:`ServiceContainer` — built once per
  :func:`~robotsix_auto_mail.server.handlers.make_board_handler` call (and
  therefore available before ``BaseHTTPRequestHandler.__init__`` dispatches
  the first request).  It lazily instantiates and memoises each registered
  service with the container's injected dependencies.

No mixins are migrated onto services yet; this is the foundation the later
migration tickets build on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar, cast

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailAccountsConfig, MailConfig


class Service:
    """Base class for a stateless board-endpoint service.

    Subclasses hold only the injected dependencies passed here.  They must
    NOT store per-request state — that stays on the handler/context (see
    :class:`robotsix_auto_mail.server._board_handler_protocol.RequestContext`).
    """

    def __init__(
        self,
        *,
        db_path: str,
        mail_config: MailConfig | None = None,
        accounts: MailAccountsConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = accounts


S = TypeVar("S", bound=Service)


class ServiceContainer:
    """Hold stateless service instances wired with injected dependencies.

    Constructed once per
    :func:`~robotsix_auto_mail.server.handlers.make_board_handler` call so it
    is available before ``BaseHTTPRequestHandler.__init__`` synchronously
    dispatches ``do_GET``/``do_POST``.  :meth:`get` builds each registered
    service on first use and memoises it, so every request reuses the same
    stateless instance.
    """

    def __init__(
        self,
        *,
        db_path: str,
        mail_config: MailConfig | None = None,
        accounts: MailAccountsConfig | None = None,
    ) -> None:
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = accounts
        self._instances: dict[type[Service], Service] = {}

    def get(self, service_cls: type[S]) -> S:
        """Return the memoised instance of *service_cls*.

        Builds it once with the container's injected dependencies and caches
        it for the container's lifetime.
        """
        instance = self._instances.get(service_cls)
        if instance is None:
            instance = service_cls(
                db_path=self.db_path,
                mail_config=self.mail_config,
                accounts=self.accounts,
            )
            self._instances[service_cls] = instance
        return cast("S", instance)
