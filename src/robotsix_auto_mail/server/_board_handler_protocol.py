"""Protocol describing the request-context surface services receive.

The composition-era services (``server/_*_service.py``) never depend on the
concrete :class:`~robotsix_auto_mail.server.handlers.BoardHandler`; they depend
on the narrow :data:`RequestContext` contract defined here.  The running
handler *is* the context — it structurally satisfies this Protocol — so
``do_GET`` / ``do_POST`` pass ``cast("RequestContext", self)`` to each service
method.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class BoardHandlerProtocol(Protocol):
    """Structural interface every board-endpoint service reads off the handler."""

    db_path: str
    mail_config: object | None  # MailConfig | None
    accounts: object | None  # MailAccountsConfig | None
    _current_account_id: str | None
    _aggregate: bool
    _account_cookie: str | None

    # Per-request transport surface, populated by
    # ``BaseHTTPRequestHandler``.  The shared request helpers
    # (:mod:`robotsix_auto_mail.server._request_helpers`) read the body and
    # headers through these; ``path`` carries the request line (with any
    # query string) for route-local parsing.
    path: str
    headers: Any
    rfile: Any
    # The ``HTTPServer`` instance, populated by ``BaseHTTPRequestHandler``.
    # Settings-page writes reach the handler-factory keywords through it to
    # refresh the running server's cached accounts after a config change.
    server: Any

    def _send_response(
        self,
        body: bytes | str,
        status: int = 200,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        pass

    def _redirect(self, location: str, code: int = 301) -> None:
        pass

    def _not_found(self) -> None:
        pass

    def _bad_request(self, message: str) -> None:
        pass

    def _problem(
        self,
        status: int,
        kind: str,
        title: str,
        detail: str,
        instance: str | None = None,
    ) -> None:
        pass

    def _serve_json(self, payload: object, status: int = 200) -> None:
        pass

    def _launch_background_worker(
        self,
        watermark_key: str,
        target: Callable[..., None] | None = None,
        args: tuple[Any, ...] = (),
        *,
        running_check: Callable[[str | None], bool] | None = None,
        precheck: Callable[[Any], bool] | None = None,
        db_path: str | None = None,
        redirect: bool = True,
    ) -> bool:
        pass

    def _read_json_object_body(
        self,
        *,
        allow_empty: bool = False,
        object_error: str = "JSON body must be an object",
    ) -> dict[str, Any] | None:
        pass

    @property
    def _effective_archive_root(self) -> str:
        pass

    def _require_imap_configured(self) -> bool:
        pass

    def _validate_archive_path(self, *folders: str) -> tuple[bool, str]:
        pass


# The request-scoped context that composition-era services receive.  It is
# the same structural surface as ``BoardHandlerProtocol`` — the running
# ``BoardHandler`` instance *is* the context — but named for the composition
# design so service code and the shared request helpers can depend on the
# narrow contract rather than on the concrete ``BoardHandler``.
RequestContext = BoardHandlerProtocol
