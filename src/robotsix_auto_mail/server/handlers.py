"""Request handler and factory for the board server.

``BoardHandler`` is a thin routing + HTTP-infrastructure shell that inherits
only from :class:`http.server.BaseHTTPRequestHandler`.  It owns the routing
tables (``do_GET`` / ``do_POST`` / ``do_PUT``), per-request account selection
(``_select_account``) and the HTTP-infrastructure methods (``_send_response``,
``_redirect``, ``_serve_json``, ``_problem``, …), plus the shared archive
guards (``_effective_archive_root``, ``_require_imap_configured``,
``_validate_archive_path``) that the services reach through the request
context.  The public API (``BoardHandler``, ``make_board_handler``) is
unchanged.

Composition architecture
-------------------------
All endpoint logic lives in stateless *services*
(``server/_*_service.py``), each a
:class:`~robotsix_auto_mail.server._services.Service` subclass, held in a
:class:`~robotsix_auto_mail.server._services.ServiceContainer` that is built
once per :func:`make_board_handler` call and exposed as ``self._services``.
Every service method receives the running handler as a request *context*
(:class:`~robotsix_auto_mail.server._board_handler_protocol.RequestContext`)
and reads all per-request state (the resolved account, ``_aggregate``, the
transport, the response sinks) off it, never off the service instance — so a
single per-connection container is safe.  The routing tables dispatch each
path to ``self._services.get(SomeService).handle_...(ctx)``.  The shared
request helpers ``parse_request_body`` and ``handle_post_action`` live in
:mod:`robotsix_auto_mail.server._request_helpers`.
"""

from __future__ import annotations

import functools
import json
import logging
from collections.abc import Callable, Mapping
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, unquote, urlsplit

from robotsix_auto_mail.config import (
    DEFAULT_ARCHIVE_ROOT,
    ConfigurationError,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.server._account_service import AccountService
from robotsix_auto_mail.server._action_service import ActionService
from robotsix_auto_mail.server._archive_service import ArchiveService
from robotsix_auto_mail.server._attachment_service import AttachmentService
from robotsix_auto_mail.server._auth_service import AuthService
from robotsix_auto_mail.server._batch_service import BatchService
from robotsix_auto_mail.server._compose_service import ComposeService
from robotsix_auto_mail.server._config_service import ConfigService
from robotsix_auto_mail.server._constants import (
    _STATIC_CHAT_SKILL_MD,
    GLOBAL_VIEW_ACCOUNT_ID,
    _with_db,
)
from robotsix_auto_mail.server._ingest_service import IngestService
from robotsix_auto_mail.server._mailbox_service import MailboxService
from robotsix_auto_mail.server._reconcile_service import ReconcileService
from robotsix_auto_mail.server._request_helpers import launch_background_worker
from robotsix_auto_mail.server._sent_service import SentService
from robotsix_auto_mail.server._services import ServiceContainer
from robotsix_auto_mail.server._settings_service import SettingsService
from robotsix_auto_mail.server._triage_service import TriageService
from robotsix_auto_mail.server._view_service import ViewService

if TYPE_CHECKING:
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext

logger = logging.getLogger(__name__)


class BoardHandler(BaseHTTPRequestHandler):
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
        # Composition-era service container, holding the stateless endpoint
        # services wired with the injected dependencies.  Built here — before
        # ``super().__init__`` synchronously dispatches ``do_GET``/``do_POST``
        # via ``handle()`` — so it is always available during request
        # dispatch.  Services read per-request state (the resolved account,
        # ``_aggregate``, the transport) off this handler, never off
        # themselves, so a per-connection container is safe.  See
        # ``_request_helpers`` for the mixin→service migration recipe.
        self._services = ServiceContainer(
            db_path=db_path,
            mail_config=mail_config,
            accounts=accounts,
        )
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    def do_GET(self) -> None:
        """Route GET requests via an ordered (predicate → handler) table."""
        # Migrated endpoints are dispatched through ``self._services``; the
        # running handler is the request *context* (``RequestContext`` — the
        # same structural surface as ``BoardHandlerProtocol``).  The cast is a
        # no-op at runtime that lets the concrete handler be passed where the
        # narrow protocol is expected.
        ctx = cast("RequestContext", self)
        # /auth-status is cross-account by design — handle before
        # _select_account() so it works regardless of the session account.
        if self.path.split("?")[0] == "/auth-status":
            self._services.get(AuthService).handle_auth_status(ctx)
            return
        # /add-account is also cross-account — handle before
        # _select_account() so account creation works even with zero accounts.
        if self.path.split("?")[0] == "/add-account":
            self._services.get(AccountService).serve_add_account(ctx)
            return
        # /accounts is cross-account by design — list every configured
        # mailbox regardless of the session account.
        if urlsplit(self.path).path == "/accounts":
            self._serve_accounts()
            return
        # The config surface covers every account at once, so it must be
        # reachable before an account is selected (and with none configured).
        if urlsplit(self.path).path == "/config":
            self._services.get(SettingsService).handle_get_config(ctx)
            return
        if urlsplit(self.path).path == "/config/versions":
            self._services.get(SettingsService).handle_get_config_versions(ctx)
            return
        if self.accounts is not None and not self._select_account():
            return
        # Dispatch on the bare path so ``?account=<id>`` query strings do
        # not defeat route matching (``self.path`` retains the query for
        # the existing query parsing inside individual handlers).
        path = urlsplit(self.path).path
        routes: list[tuple[Callable[[str], bool], Callable[[], None]]] = [
            (lambda p: p == "/", lambda: self._redirect("/board")),
            (
                lambda p: p == "/board",
                lambda: self._services.get(ViewService).serve_board(ctx),
            ),
            (
                lambda p: p == "/board-content",
                lambda: self._services.get(ViewService).serve_board_content(ctx),
            ),
            (
                lambda p: p == "/board-cards",
                lambda: self._services.get(ViewService).serve_board_cards(ctx),
            ),
            (lambda p: p == "/chat-skill", self._serve_chat_skill),
            (lambda p: p == "/health", self._serve_health),
            (lambda p: p == "/healthz", self._serve_health),
            (lambda p: p == "/ready", self._serve_ready),
            (lambda p: p == "/readyz", self._serve_ready),
            (
                lambda p: p == "/settings-panel",
                lambda: self._services.get(SettingsService).serve_settings_panel(ctx),
            ),
            (
                lambda p: p == "/probe-health",
                self._serve_probe_health,
            ),
            (
                lambda p: p == "/llm/provider-status",
                self._serve_llm_provider_status,
            ),
            (
                lambda p: p == "/archive-folders",
                lambda: self._services.get(ViewService).serve_archive_folders(ctx),
            ),
            (lambda p: p == "/archive-log", self._serve_archive_log),
            (
                lambda p: p.startswith("/archive/") and p.endswith("/messages"),
                lambda: self._services.get(ViewService).serve_archive_messages(
                    ctx,
                    folder=unquote(
                        urlsplit(self.path).path[len("/archive/") : -len("/messages")]
                    ),
                ),
            ),
            (
                lambda p: p == "/sent/messages",
                lambda: self._services.get(SentService).serve_sent_messages(ctx),
            ),
            (
                lambda p: p == "/sent/message",
                lambda: self._services.get(SentService).serve_sent_message(ctx),
            ),
            (
                lambda p: p == "/folders",
                lambda: self._services.get(MailboxService).serve_folders(ctx),
            ),
            (
                lambda p: p == "/search",
                lambda: self._services.get(MailboxService).serve_search(ctx),
            ),
            (
                lambda p: p.startswith("/static/"),
                lambda: self._services.get(ViewService).serve_static(ctx),
            ),
            (
                lambda p: p.startswith("/email/") and p.endswith("/status"),
                lambda: self._services.get(ViewService).serve_email_status(ctx),
            ),
            (
                lambda p: p.startswith("/email/"),
                lambda: self._services.get(ViewService).serve_email_detail(ctx),
            ),
            (
                lambda p: p.startswith("/archive-proposal/"),
                lambda: self._services.get(ViewService).serve_archive_proposal(ctx),
            ),
        ]
        for matches, handler in routes:
            if matches(path):
                handler()
                return
        self._not_found()

    def do_POST(self) -> None:
        """Route POST requests via an exact-match table."""
        # Migrated endpoints are dispatched through ``self._services``; the
        # running handler is the request *context* (``RequestContext`` — the
        # same structural surface as ``BoardHandlerProtocol``).  The cast is a
        # no-op at runtime that lets the concrete handler be passed where the
        # narrow protocol is expected.
        ctx = cast("RequestContext", self)
        # /auth-start is cross-account by design — handle before
        # _select_account() so it works regardless of the session account.
        if urlsplit(self.path).path == "/auth-start":
            self._services.get(AuthService).handle_auth_start(ctx)
            return
        # /add-account is also cross-account — handle before
        # _select_account() so account creation works even with zero accounts.
        if urlsplit(self.path).path == "/add-account":
            self._services.get(AccountService).handle_add_account(ctx)
            return
        # /delete-account is also cross-account — handle before
        # _select_account() so account deletion works even when the
        # deleted account is not the currently-selected one.
        if urlsplit(self.path).path == "/delete-account":
            self._services.get(SettingsService).handle_delete_account(ctx)
            return
        # Rollback covers every account at once — same reasoning as GET /config.
        if urlsplit(self.path).path == "/config/rollback":
            self._services.get(SettingsService).handle_config_rollback(ctx)
            return
        if self.accounts is not None and not self._select_account():
            return
        # Dispatch on the bare path so ``?account=<id>`` query strings do
        # not defeat exact-match routing.
        path = urlsplit(self.path).path

        # Prefix-based POST routes (before exact-match table).
        if path.startswith("/email/") and path.endswith("/attachments/to-file-hub"):
            # Extract message_id from /email/<message_id>/attachments/to-file-hub
            suffix = "/attachments/to-file-hub"
            message_id = unquote(path[len("/email/") : -len(suffix)])
            if message_id:
                self._services.get(AttachmentService).handle_push_to_file_hub(
                    ctx, message_id
                )
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
            "/move": lambda: self._services.get(ActionService).handle_move(ctx),
            "/delete": lambda: self._services.get(ActionService).handle_delete(ctx),
            "/archive": lambda: self._services.get(ArchiveService).handle_archive(ctx),
            "/archive-move": lambda: self._services.get(
                ArchiveService
            ).handle_archive_move(ctx),
            "/archive-delete": lambda: self._services.get(
                ArchiveService
            ).handle_archive_delete(ctx),
            "/archive-message-delete": lambda: self._services.get(
                ArchiveService
            ).handle_archive_message_delete(ctx),
            "/archive-rename": lambda: self._services.get(
                ArchiveService
            ).handle_archive_rename(ctx),
            "/batch-delete": lambda: self._services.get(
                BatchService
            ).handle_batch_delete(ctx),
            "/batch-archive": lambda: self._services.get(
                BatchService
            ).handle_batch_archive(ctx),
            "/batch-archive-folder": lambda: self._services.get(
                BatchService
            ).handle_batch_archive_folder(ctx),
            "/config-sync": lambda: self._services.get(
                ConfigService
            ).handle_config_sync(ctx),
            "/run-triage": lambda: self._services.get(TriageService).handle_run_triage(
                ctx
            ),
            "/reconcile": lambda: self._services.get(ReconcileService).handle_reconcile(
                ctx
            ),
            "/force-fetch": lambda: self._services.get(
                IngestService
            ).handle_force_fetch(ctx),
            "/force-triage-column": lambda: self._services.get(
                TriageService
            ).handle_force_triage_column(ctx),
            "/archive-proposal": lambda: self._services.get(
                ConfigService
            ).handle_archive_proposal(ctx),
            "/save-notes": lambda: self._services.get(ActionService).handle_save_notes(
                ctx
            ),
            "/compose-draft": lambda: self._services.get(
                ComposeService
            ).handle_compose_draft(ctx),
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
        ctx = cast("RequestContext", self)
        if urlsplit(self.path).path == "/config":
            self._services.get(SettingsService).handle_put_config(ctx)
            return
        self._not_found()

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
        """Delegate to the shared ``launch_background_worker`` helper.

        Kept as a handler method so the composition-era services (batch,
        ingest, reconcile, triage) can guard their background work through
        ``ctx._launch_background_worker`` — the shared single-flight watermark
        implementation now lives in :mod:`._request_helpers`.
        """
        return launch_background_worker(
            cast("RequestContext", self),
            watermark_key,
            target,
            args,
            running_check=running_check,
            precheck=precheck,
            db_path=db_path,
            redirect=redirect,
        )

    @property
    def _effective_archive_root(self) -> str:
        """The configured archive root, or the default when no config is set."""
        return (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

    def _require_imap_configured(self) -> bool:
        """Guard: emit 503 when ``mail_config`` is unset.

        Returns ``True`` when IMAP is configured and the caller may
        proceed; ``False`` when a 503 response has already been sent
        and the caller must ``return`` immediately.
        """
        if self.mail_config is None:
            self._serve_json(
                {"error": "IMAP not configured for this account"},
                status=503,
            )
            return False
        return True

    def _validate_archive_path(self, *folders: str) -> tuple[bool, str]:
        """Guard: reject ``..`` segments in archive folder paths.

        Returns ``(True, archive_root)`` when all *folders* are safe.
        Returns ``(False, "")`` after sending a 400 response when any
        path contains a ``..`` segment.
        """
        archive_root = self._effective_archive_root
        for folder in folders:
            if ".." in folder.split("/"):
                self._bad_request(f"'{folder}' escapes archive root")
                return False, ""
        return True, archive_root

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
        """Send a 404 Not Found as an RFC 7807 problem response."""
        self._problem(
            status=404,
            kind="not-found",
            title="Not Found",
            detail="The requested resource was not found.",
        )

    def _bad_request(self, message: str) -> None:
        """Send a 400 Bad Request as an RFC 7807 problem response."""
        self._problem(
            status=400,
            kind="bad-request",
            title="Bad Request",
            detail=message,
        )

    def _serve_json(self, payload: Mapping[str, object], status: int = 200) -> None:
        """Serialize *payload* as JSON and send it with *status*."""
        self._send_response(
            json.dumps(payload),
            status=status,
            content_type="application/json; charset=utf-8",
        )

    def _problem(
        self,
        status: int,
        kind: str,
        title: str,
        detail: str,
        instance: str | None = None,
    ) -> None:
        """Send an RFC 7807 problem-details JSON error response.

        Every API error response uses this single envelope so clients can
        parse one shape: ``{"type": "urn:robotsix:error:<kind>", "title",
        "detail", "instance": self.path}``.  *instance* defaults to the
        request path (as recommended by RFC 7807).
        """
        self._serve_json(
            {
                "type": f"urn:robotsix:error:{kind}",
                "title": title,
                "detail": detail,
                "instance": instance if instance is not None else self.path,
            },
            status=status,
        )

    def _serve_chat_skill(self) -> None:
        """Serve GET /chat-skill — Claude SKILL.md for the chat-access standard."""
        self._send_response(
            _STATIC_CHAT_SKILL_MD,
            content_type="text/markdown; charset=utf-8",
        )

    def _serve_accounts(self) -> None:
        """Serve GET /accounts — list configured mail accounts as JSON.

        Returns ``{"accounts": [{"id": …, "address": …, "label": …,
        "healthy": …}, …]}``.  Read-only — no side effects.
        """
        from robotsix_auto_mail.db.queries import get_account_health

        if self.accounts is None:
            self._serve_json({"accounts": []})
            return

        result: list[dict[str, object]] = []
        for account in self.accounts.accounts:
            healthy: bool | None = None
            try:
                with _with_db(account.config.db_path, skip_migrations=True) as conn:
                    health = get_account_health(conn)
                if health is not None:
                    healthy = health.get("status") == "ok"
            except Exception:
                logger.debug(
                    "Could not check health for account %s",
                    account.account_id,
                    exc_info=True,
                )
            result.append(
                {
                    "id": account.account_id,
                    "address": account.config.username,
                    "label": account.label,
                    "healthy": healthy,
                }
            )

        self._serve_json({"accounts": result})

    def _serve_health(self) -> None:
        """Serve GET /health — liveness check."""
        self._serve_json({"status": "ok"}, status=200)

    def _serve_llm_provider_status(self) -> None:
        """Serve GET /llm/provider-status — llmio provider-failover state.

        Surfaces which provider slot (default Anthropic / fallback
        OpenRouter) is serving LLM calls and, while failover is armed,
        when the default slot returns.
        """
        from robotsix_llmio.core import get_failover_status

        self._serve_json(get_failover_status().model_dump(mode="json"), status=200)

    def _serve_ready(self) -> None:
        """Serve GET /readyz — readiness check (verifies the SQLite store)."""
        try:
            with _with_db(self.db_path) as conn:
                conn.execute("SELECT 1")
        except Exception as exc:  # datastore unreachable
            self._serve_json({"status": "unavailable", "error": str(exc)}, status=503)
            return
        self._serve_json({"status": "ready"}, status=200)

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
