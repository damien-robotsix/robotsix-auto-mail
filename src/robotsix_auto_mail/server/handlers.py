"""Request handler and factory for the board server.

``BoardHandler`` is assembled from six private mixin classes via multiple
inheritance; each mixin lives in its own module under ``server/``:

- ``_view_mixin`` — GET view methods (``_serve_board``, …)
- ``_action_mixin`` — POST action methods (``_handle_move``, …)
- ``_batch_mixin`` — batch delete / archive handlers
- ``_triage_mixin`` — triage launcher and rule-action handlers
- ``_draft_mixin`` — draft save / send / generate handlers
- ``_config_mixin`` — config-sync and archive-proposal handlers

``BoardHandler`` itself retains the routing tables (``do_GET`` /
``do_POST``), account selection, and the HTTP-infrastructure methods
(``_send_response``, ``_redirect``, …).  The public API
(``BoardHandler``, ``make_board_handler``) is unchanged.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlsplit

from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.server._action_mixin import _BoardActionMixin
from robotsix_auto_mail.server._batch_mixin import _BatchActionMixin
from robotsix_auto_mail.server._config_mixin import _ConfigMixin
from robotsix_auto_mail.server._constants import GLOBAL_VIEW_ACCOUNT_ID
from robotsix_auto_mail.server._draft_mixin import _DraftMixin
from robotsix_auto_mail.server._triage_mixin import _TriageMixin
from robotsix_auto_mail.server._view_mixin import _BoardViewMixin


class BoardHandler(
    _BoardViewMixin,
    _BoardActionMixin,
    _BatchActionMixin,
    _TriageMixin,
    _DraftMixin,
    _ConfigMixin,
    BaseHTTPRequestHandler,
):
    """Request handler for the robotsix-auto-mail board server.

    Routes ``GET /`` to a 301 redirect to ``/board``, ``GET /board`` to
    the kanban board HTML page, and everything else to 404.  The target
    SQLite database is injected per-instance via ``db_path``.
    """

    def __init__(
        self,
        *args: object,
        db_path: str,
        mail_config: MailConfig | None = None,
        accounts: MailAccountsConfig | None = None,
        default_account_id: str | None = None,
        **kwargs: object,
    ) -> None:
        # Set attributes BEFORE calling ``super().__init__`` because
        # ``BaseHTTPRequestHandler.__init__`` invokes ``handle()``
        # synchronously, which dispatches to ``do_GET``/``do_POST``.
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = accounts
        self.default_account_id = default_account_id
        # ``Set-Cookie`` value emitted by the response sinks when a
        # request selected an account via ``?account=`` (set by
        # ``_select_account``); ``None`` means no cookie is written.
        self._account_cookie: str | None = None
        # Resolved current account id for the in-flight request (set by
        # ``_select_account``); ``None`` in legacy single-account mode
        # because ``_select_account`` is never called there.
        self._current_account_id: str | None = None
        # Aggregate mode flag — set to ``True`` when the request resolves to
        # the global (all-accounts) board view.
        self._aggregate: bool = False
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:
        """Route GET requests via an ordered (predicate → handler) table."""
        if self.accounts is not None and not self._select_account():
            return
        # Dispatch on the bare path so ``?account=<id>`` query strings do
        # not defeat route matching (``self.path`` retains the query for
        # the existing query parsing inside individual handlers).
        path = urlsplit(self.path).path
        routes: list[tuple[Callable[[str], bool], Callable[[], None]]] = [
            (lambda p: p == "/", lambda: self._redirect("/board")),
            (lambda p: p == "/board", self._serve_board),
            (lambda p: p == "/board-content", self._serve_board_content),
            (lambda p: p == "/folders", self._serve_folders),
            (lambda p: p.startswith("/static/"), self._serve_static),
            (
                lambda p: p.startswith("/email/") and p.endswith("/status"),
                self._serve_email_status,
            ),
            (lambda p: p.startswith("/email/"), self._serve_email_detail),
            (
                lambda p: p.startswith("/archive-proposal/"),
                self._serve_archive_proposal,
            ),
        ]
        for matches, handler in routes:
            if matches(path):
                handler()
                return
        self._not_found()

    def do_POST(self) -> None:
        """Route POST requests via an exact-match table."""
        if self.accounts is not None and not self._select_account():
            return
        # Periodic-trigger decision — Option A (on-demand endpoint
        # only): no background/periodic runner is added.  The
        # deterministic ``check_config_sync.py`` remains the fast, free,
        # blocking CI gate; the LLM agent is an optional advisory tool,
        # so it does not need to run on a schedule.  The board server is
        # a single-threaded ``BaseHTTPRequestHandler``/``HTTPServer``
        # with no scheduler — adding a ``while True``/``time.sleep`` loop
        # would block request serving and is out of scope.  External
        # schedulers (cron, systemd timer) can simply POST to
        # ``/config-sync``, which fully satisfies optional periodic
        # invocation without new in-process machinery.  Option B (an
        # in-process periodic runner) is explicitly deferred.
        routes: dict[str, Callable[[], None]] = {
            "/move": self._handle_move,
            "/delete": self._handle_delete,
            "/archive": self._handle_archive,
            "/batch-delete": self._handle_batch_delete,
            "/batch-archive": self._handle_batch_archive,
            "/rule-action": self._handle_rule_action,
            "/config-sync": self._handle_config_sync,
            "/run-triage": self._handle_run_triage,
            "/run-folder-triage": self._handle_run_folder_triage,
            "/force-triage-column": self._handle_force_triage_column,
            "/archive-proposal": self._handle_archive_proposal,
            "/save-notes": self._handle_save_notes,
            "/save-draft": self._handle_save_draft,
            "/send-draft": self._handle_send_draft,
            "/generate-draft": self._handle_generate_draft,
        }
        # Dispatch on the bare path so ``?account=<id>`` query strings do
        # not defeat exact-match routing.
        handler = routes.get(urlsplit(self.path).path)
        if handler is None:
            self._not_found()
            return
        handler()

    def _select_account(self) -> bool:
        """Resolve the per-request account and bind its DB / mail config.

        Only invoked when ``self.accounts is not None``.  Resolution
        precedence: ``?account=`` query param → ``account`` request
        cookie → ``self.default_account_id``/the container default.

        The reserved sentinel ``GLOBAL_VIEW_ACCOUNT_ID`` (``"__all__"``)
        selects the aggregate view instead of a single account.  When
        there is no ``?account=``, no cookie, and at least two accounts
        are configured, the handler defaults to the aggregate view.

        An explicit ``?account=<id>`` that is unknown is a hard 404
        (returns ``False`` so the caller skips dispatch).  A stale id
        coming only from the cookie is ignored — cookies must never
        hard-fail a request.  On success, ``self.db_path`` and
        ``self.mail_config`` are rebound to the selected account for the
        duration of the request and a ``Set-Cookie`` is armed when the id
        arrived via the query param.  Returns ``True`` on success.
        """
        accounts = self.accounts
        if accounts is None:  # pragma: no cover - guarded by the caller
            return True
        query = parse_qs(urlsplit(self.path).query)
        query_values = query.get("account")
        query_id = query_values[0] if query_values else None

        cookie_id: str | None = None
        cookie_header = self.headers.get("Cookie")
        if cookie_header:
            morsel = SimpleCookie(cookie_header).get("account")
            if morsel is not None:
                cookie_id = morsel.value

        # -- aggregate-mode resolution -----------------------------------
        if query_id == GLOBAL_VIEW_ACCOUNT_ID:
            self._aggregate = True
            self._account_cookie = f"account={GLOBAL_VIEW_ACCOUNT_ID}; Path=/"
            self._current_account_id = GLOBAL_VIEW_ACCOUNT_ID
            return True

        if cookie_id == GLOBAL_VIEW_ACCOUNT_ID and not query_id:
            self._aggregate = True
            self._current_account_id = GLOBAL_VIEW_ACCOUNT_ID
            return True

        # No query param, no cookie, ≥2 accounts → default to aggregate.
        if not query_id and not cookie_id and len(accounts.ids()) >= 2:
            self._aggregate = True
            self._current_account_id = GLOBAL_VIEW_ACCOUNT_ID
            return True

        # -- single-account resolution -----------------------------------
        fallback_id = self.default_account_id or accounts.default_account_id
        account_id = query_id or cookie_id or fallback_id

        try:
            account = accounts.get(account_id)
        except ConfigurationError:
            if query_id is not None:
                # Explicit, unknown account → hard 404.
                self._not_found()
                return False
            # Stale/unknown cookie id → fall back to the default account.
            account = accounts.get(fallback_id)

        self.db_path = account.config.db_path
        self.mail_config = account.config
        self._current_account_id = account.account_id
        if query_id is not None:
            self._account_cookie = f"account={account.account_id}; Path=/"
        return True

    def _send_response(
        self,
        body: bytes | str,
        status: int = 200,
        content_type: str = "text/plain; charset=utf-8",
    ) -> None:
        """Write a complete response (status line, headers, body).

        The single place that writes response headers + body — all
        handler methods delegate here (the only other writer is
        ``_redirect``, which emits a bodiless ``Location`` redirect).
        """
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        if self._account_cookie is not None:
            self.send_header("Set-Cookie", self._account_cookie)
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self, location: str, code: int = 301) -> None:
        """Send a redirect to *location*.

        Defense-in-depth at the sink: if *location* carries any CR/LF
        or other ASCII control character (which could split the HTTP
        response and inject extra headers), fall back to ``/board`` so
        the ``Location`` header can never carry such a value.
        """
        if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in location):
            location = "/board"
        self.send_response(code)
        self.send_header("Location", location)
        if self._account_cookie is not None:
            self.send_header("Set-Cookie", self._account_cookie)
        self.end_headers()

    def _not_found(self) -> None:
        """Send a 404 Not Found."""
        self._send_response(b"Not found", status=404)

    def _bad_request(self, message: str) -> None:
        """Send a 400 Bad Request with a plain-text body."""
        self._send_response(message, status=400)

    def _serve_json(self, payload: Mapping[str, object], status: int = 200) -> None:
        """Serialize *payload* as JSON and send it with *status*."""
        self._send_response(
            json.dumps(payload),
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def log_message(self, format: str, *args: object) -> None:
        """Suppress logging to stderr (keep server quiet)."""
        pass


def make_board_handler(
    db_path: str,
    mail_config: MailConfig | None = None,
    *,
    accounts: MailAccountsConfig | None = None,
    default_account_id: str | None = None,
) -> functools.partial[BoardHandler]:
    """Return a callable that builds a ``BoardHandler`` wired to *db_path*.

    ``HTTPServer`` calls the result as ``handler(request, client_address,
    server)``; the returned ``functools.partial`` binds *db_path* and
    *mail_config* as keyword arguments so the standard three positional
    args still flow through to ``BoardHandler.__init__``.

    When *accounts* is provided, the handler additionally resolves the
    target account per request (query param / cookie / default), and
    *db_path*/*mail_config* act as the pre-resolution defaults.  In the
    legacy single-account mode (*accounts* ``None``) the partial binds
    only ``db_path`` and ``mail_config`` so existing callers and tests
    observe an unchanged keyword set.
    """
    if accounts is None:
        return functools.partial(
            BoardHandler,
            db_path=db_path,
            mail_config=mail_config,
        )
    return functools.partial(
        BoardHandler,
        db_path=db_path,
        mail_config=mail_config,
        accounts=accounts,
        default_account_id=default_account_id,
    )
