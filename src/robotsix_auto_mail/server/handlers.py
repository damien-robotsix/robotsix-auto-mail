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
from urllib.parse import parse_qs, unquote, urlsplit

from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.server._account_mixin import _AccountMixin
from robotsix_auto_mail.server._action_mixin import _BoardActionMixin
from robotsix_auto_mail.server._auth_mixin import _BoardAuthMixin
from robotsix_auto_mail.server._batch_mixin import _BatchActionMixin
from robotsix_auto_mail.server._config_mixin import _ConfigMixin
from robotsix_auto_mail.server._constants import (
    _STATIC_CHAT_SKILL_MD,
    GLOBAL_VIEW_ACCOUNT_ID,
    _with_db,
)
from robotsix_auto_mail.server._draft_mixin import _DraftMixin
from robotsix_auto_mail.server._reconcile_mixin import _ReconcileMixin
from robotsix_auto_mail.server._settings_mixin import _SettingsMixin
from robotsix_auto_mail.server._triage_mixin import _TriageMixin
from robotsix_auto_mail.server._view_mixin import _BoardViewMixin


class BoardHandler(
    _BoardViewMixin,
    _BoardActionMixin,
    _BatchActionMixin,
    _ReconcileMixin,
    _TriageMixin,
    _DraftMixin,
    _ConfigMixin,
    _AccountMixin,
    _BoardAuthMixin,
    _SettingsMixin,
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
        **kwargs: object,
    ) -> None:
        # Set attributes BEFORE calling ``super().__init__`` because
        # ``BaseHTTPRequestHandler.__init__`` invokes ``handle()``
        # synchronously, which dispatches to ``do_GET``/``do_POST``.
        self.db_path = db_path
        self.mail_config = mail_config
        self.accounts = accounts
        # ``Set-Cookie`` value emitted by the response sinks when a
        # request selected an account via ``?account=`` (set by
        # ``_select_account``); ``None`` means no cookie is written.
        self._account_cookie: str | None = None
        # Resolved current account id for the in-flight request (set by
        # ``_select_account``); ``None`` when ``_select_account`` is not
        # called (accounts is ``None``).
        self._current_account_id: str | None = None
        # Aggregate mode flag — set to ``True`` when the request resolves to
        # the global (all-accounts) board view.
        self._aggregate: bool = False
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:
        """Route GET requests via an ordered (predicate → handler) table."""
        # /auth-status is cross-account by design — handle before
        # _select_account() so it works regardless of the session account.
        if self.path.split("?")[0] == "/auth-status":
            self._handle_auth_status()
            return
        # /add-account is also cross-account — handle before
        # _select_account() so account creation works even with zero accounts.
        if self.path.split("?")[0] == "/add-account":
            self._serve_add_account()
            return
        # The config surface covers every account at once, so it must be
        # reachable before an account is selected (and with none configured).
        if urlsplit(self.path).path == "/config":
            self._handle_get_config()
            return
        if urlsplit(self.path).path == "/config/versions":
            self._handle_get_config_versions()
            return
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
            (lambda p: p == "/board-cards", self._serve_board_cards),
            (lambda p: p == "/chat-skill", self._serve_chat_skill),
            (lambda p: p == "/health", self._serve_health),
            (lambda p: p == "/healthz", self._serve_health),
            (lambda p: p == "/settings-panel", self._serve_settings_panel),
            (
                lambda p: p == "/probe-health",
                self._serve_probe_health,
            ),
            (
                lambda p: p == "/archive-folders",
                self._serve_archive_folders,
            ),
            (lambda p: p == "/archive-log", self._serve_archive_log),
            (
                lambda p: p.startswith("/archive/") and p.endswith("/messages"),
                lambda: self._serve_archive_messages(
                    folder=unquote(
                        urlsplit(self.path).path[len("/archive/") : -len("/messages")]
                    )
                ),
            ),
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
        if not self._check_csrf():
            return
        # /auth-start is cross-account by design — handle before
        # _select_account() so it works regardless of the session account.
        if urlsplit(self.path).path == "/auth-start":
            self._handle_auth_start()
            return
        # /add-account is also cross-account — handle before
        # _select_account() so account creation works even with zero accounts.
        if urlsplit(self.path).path == "/add-account":
            self._handle_add_account()
            return
        # /delete-account is also cross-account — handle before
        # _select_account() so account deletion works even when the
        # deleted account is not the currently-selected one.
        if urlsplit(self.path).path == "/delete-account":
            self._handle_delete_account()
            return
        # Rollback covers every account at once — same reasoning as GET /config.
        if urlsplit(self.path).path == "/config/rollback":
            self._handle_config_rollback()
            return
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
            "/archive-move": self._handle_archive_move,
            "/archive-delete": self._handle_archive_delete,
            "/archive-message-delete": self._handle_archive_message_delete,
            "/archive-rename": self._handle_archive_rename,
            "/batch-delete": self._handle_batch_delete,
            "/batch-archive": self._handle_batch_archive,
            "/batch-archive-folder": self._handle_batch_archive_folder,
            "/config-sync": self._handle_config_sync,
            "/run-triage": self._handle_run_triage,
            "/reconcile": self._handle_reconcile,
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

    def do_PUT(self) -> None:
        """Route PUT requests — the config surface is the only one."""
        if not self._check_csrf():
            return
        if urlsplit(self.path).path == "/config":
            self._handle_put_config()
            return
        self._not_found()

    def _check_csrf(self) -> bool:
        """Reject cross-origin POST requests.

        Modern browsers always include an ``Origin`` header on cross-origin
        requests (including simple ``application/x-www-form-urlencoded``
        form POSTs that do not trigger a CORS preflight).  When the header
        is present the request is accepted only if it matches one of:

        * ``Sec-Fetch-Site: same-origin`` or ``none`` — set by the browser
          itself and unforgeable by a cross-site page, checked before
          ``Origin`` because this server sends ``Referrer-Policy:
          no-referrer`` and Firefox then reports ``Origin: null`` even for a
          same-origin submission;
        * the server's own loopback origin (``127.0.0.1`` / ``localhost``
          on the bound port) — covers local dev / CLI use;
        * the request's own ``Host`` header — the standard proxy-aware
          same-origin check: when the server runs behind a reverse proxy
          the browser sets ``Origin`` and ``Host`` to the same public
          authority (e.g. ``mail.deploy.robotsix.net``);
        * the ``X-Forwarded-Host`` header — first value when
          comma-separated, stripped of whitespace, for environments where
          the reverse proxy rewrites ``Host`` but forwards the public
          host here;
        * the ``host=`` parameter of the first ``Forwarded`` (RFC 7239)
          header element;
        * the ``trusted_origins`` list in :class:`MailAccountsConfig` —
          explicit full-origin URLs (e.g. ``https://mail.deploy.robotsix.net``)
          for proxies that rewrite ``Host`` without setting forwarding headers.

        Requests without an ``Origin`` header (same-origin page navigation,
        ``curl``, CLI tools) are allowed — malicious cross-site forms cannot
        suppress the header.
        """
        # ``Sec-Fetch-Site`` is the purpose-built signal and is decided by the
        # browser, not by the page, so a cross-site attacker cannot forge it.
        # Check it first: this server sends ``Referrer-Policy: no-referrer``,
        # and Firefox then sends ``Origin: null`` on a *same-origin* form POST,
        # which every check below would reject. That combination made the
        # add-account form reject its own submission.
        fetch_site = self.headers.get("Sec-Fetch-Site")
        if fetch_site in ("same-origin", "none"):
            return True

        origin = self.headers.get("Origin")
        if origin is None:
            return True
        server_port = self.server.server_address[1]  # type: ignore[index]
        allowed = {
            f"http://127.0.0.1:{server_port}",
            f"http://localhost:{server_port}",
        }
        if origin in allowed:
            return True
        origin_netloc = urlsplit(origin).netloc
        # Proxy-aware same-origin check: when the server runs behind a
        # reverse proxy the browser sends Origin and Host set to the
        # same public authority (e.g. ``mail.deploy.robotsix.net``).
        host = self.headers.get("Host")
        if host is not None and origin_netloc == host:
            return True
        # ``X-Forwarded-Host``: first value when comma-separated.
        xfh = self.headers.get("X-Forwarded-Host")
        if xfh is not None:
            first_xfh = xfh.split(",")[0].strip()
            if origin_netloc == first_xfh:
                return True
        # ``Forwarded`` (RFC 7239): ``host=`` parameter of first element.
        fwd = self.headers.get("Forwarded")
        if fwd is not None:
            first_element = fwd.split(",")[0].strip()
            for param in first_element.split(";"):
                param = param.strip()
                if param.startswith("host="):
                    fwd_host = param[len("host=") :].strip()
                    if fwd_host.startswith('"') and fwd_host.endswith('"'):
                        fwd_host = fwd_host[1:-1]
                    if origin_netloc == fwd_host:
                        return True
                    break
        # Explicitly configured trusted origins — the operator can list
        # the public origin(s) (e.g. ``https://mail.deploy.robotsix.net``)
        # when the reverse proxy rewrites ``Host`` without setting
        # ``X-Forwarded-Host``.
        trusted = self.accounts.trusted_origins if self.accounts is not None else ()
        if origin in trusted:
            return True
        self._send_response("Forbidden: cross-origin request rejected", status=403)
        return False

    def _select_account(self) -> bool:
        """Resolve the per-request account and bind its DB / mail config.

        Only invoked when ``self.accounts is not None``.  Resolution
        precedence: ``?account=`` query param → ``account`` request
        cookie → first account in configured order (initial view).

        The reserved sentinel ``GLOBAL_VIEW_ACCOUNT_ID`` (``"__all__"``)
        selects the aggregate view instead of a single account.  The
        aggregate view is only entered when explicitly requested via
        ``?account=__all__`` or a pre-existing ``account=__all__`` cookie.

        When there is no ``?account=``, no cookie, and at least one account
        is configured, the handler defaults to the first account in
        configured order and sets the ``account`` cookie so the selection
        persists across subsequent requests.

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
        # Zero configured accounts → nothing to resolve; serve the request
        # account-less so endpoints like /health keep working at startup.
        if not accounts.ids():
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
            self._account_cookie = self._build_account_cookie(GLOBAL_VIEW_ACCOUNT_ID)
            self._current_account_id = GLOBAL_VIEW_ACCOUNT_ID
            return True

        if cookie_id == GLOBAL_VIEW_ACCOUNT_ID and not query_id:
            self._aggregate = True
            self._current_account_id = GLOBAL_VIEW_ACCOUNT_ID
            return True

        # -- explicit account resolution via query param -----------------
        if query_id is not None:
            try:
                account = accounts.get(query_id)
            except ConfigurationError:
                # Explicit, unknown account → hard 404.
                self._not_found()
                return False
            self.db_path = account.config.db_path
            self.mail_config = account.config
            self._current_account_id = account.account_id
            self._account_cookie = self._build_account_cookie(account.account_id)
            return True

        # -- cookie-based resolution ------------------------------------
        if cookie_id is not None:
            try:
                account = accounts.get(cookie_id)
            except ConfigurationError:
                # Stale/unknown cookie id → fall through to first account.
                pass
            else:
                self.db_path = account.config.db_path
                self.mail_config = account.config
                self._current_account_id = account.account_id
                return True

        # -- fallback: first account in configured order -----------------
        first_account = accounts.accounts[0]
        self.db_path = first_account.config.db_path
        self.mail_config = first_account.config
        self._current_account_id = first_account.account_id
        return True

    def _build_account_cookie(self, account_id: str) -> str:
        """Return a ``Set-Cookie`` value for *account_id*.

        Always includes ``HttpOnly`` (block JavaScript access) and
        ``SameSite=Lax`` (prevent CSRF on state-changing requests).
        ``Secure`` is added when the request arrived via a TLS-terminating
        reverse proxy that sets ``X-Forwarded-Proto: https``.
        """
        suffix = "; Path=/; HttpOnly; SameSite=Lax"
        if self.headers.get("X-Forwarded-Proto") == "https":
            suffix += "; Secure"
        return f"account={account_id}{suffix}"

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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'self'",
        )
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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "frame-ancestors 'self'",
        )
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

    def _serve_chat_skill(self) -> None:
        """Serve GET /chat-skill — Claude SKILL.md for the chat-access standard."""
        self._send_response(
            _STATIC_CHAT_SKILL_MD,
            content_type="text/markdown; charset=utf-8",
        )

    def _serve_health(self) -> None:
        """Serve GET /health — liveness check."""
        self._serve_json({"status": "ok"}, status=200)

    def _serve_archive_log(self) -> None:
        """Serve GET /archive-log — read-only archive audit trail.

        Query params:
        - ``limit`` (int, default 100): max entries to return.
        - ``since`` (ISO 8601): only entries archived at or after this time.
        - ``folder`` (str): only entries whose ``dest_folder`` equals this value.
        """
        from robotsix_auto_mail.db import list_archive_audit_entries

        params = parse_qs(urlsplit(self.path).query)
        limit_str = params.get("limit", ["100"])[0]
        since = params.get("since", [None])[0]
        folder = params.get("folder", [None])[0]

        try:
            limit = int(limit_str)
        except ValueError, TypeError:
            self._bad_request("limit must be an integer")
            return

        if limit < 1 or limit > 1000:
            self._bad_request("limit must be between 1 and 1000")
            return

        with _with_db(self.db_path) as conn:
            entries = list_archive_audit_entries(
                conn, limit=limit, since=since, folder=folder
            )
        self._serve_json({"entries": entries}, status=200)

    def _serve_probe_health(self) -> None:
        """Serve GET /probe-health — on-demand IMAP + SMTP connectivity probe.

        Iterates all configured accounts, probes each one, persists the result
        in each account's ``account_health`` watermark, and returns a JSON
        summary.
        """
        from robotsix_auto_mail.core.health import probe_account, utcnow
        from robotsix_auto_mail.db.queries import write_account_health

        accounts = self.accounts
        if accounts is None:
            self._serve_json({"accounts": {}}, status=200)
            return

        result: dict[str, dict[str, str | None]] = {}
        for account in accounts.accounts:
            status, error = probe_account(account.config)
            with _with_db(account.config.db_path) as conn:
                write_account_health(
                    conn,
                    status=status,
                    error=error,
                    checked_at=utcnow(),
                )
            result[account.account_id] = {"status": status, "error": error}

        self._serve_json({"accounts": result}, status=200)

    def log_message(self, format: str, *args: object) -> None:
        """Log HTTP access via the structlog-enabled logger."""
        import logging

        logging.getLogger("robotsix_auto_mail.http.access").info(
            "%s - %s",
            self.client_address[0],
            format % args,
        )


def make_board_handler(
    db_path: str,
    mail_config: MailConfig | None = None,
    *,
    accounts: MailAccountsConfig | None = None,
) -> functools.partial[BoardHandler]:
    """Return a callable that builds a ``BoardHandler`` wired to *db_path*.

    ``HTTPServer`` calls the result as ``handler(request, client_address,
    server)``; the returned ``functools.partial`` binds *db_path* and
    *mail_config* as keyword arguments so the standard three positional
    args still flow through to ``BoardHandler.__init__``.

    When *accounts* is provided, the handler additionally resolves the
    target account per request (query param / cookie / first account),
    and *db_path*/*mail_config* act as the pre-resolution defaults.  When
    *accounts* is ``None`` the partial binds only ``db_path`` and
    ``mail_config`` so existing callers and tests observe an unchanged
    keyword set.

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
    )
