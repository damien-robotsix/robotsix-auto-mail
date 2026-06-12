"""Request handler and factory for the board server."""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Mapping
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from robotsix_auto_mail.config import (
    DEFAULT_ARCHIVE_ROOT,
    ConfigurationError,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.db import MailRecord
from robotsix_auto_mail.server._constants import (
    _STATIC_AUTOMAIL_BOARD_CSS,
    _STATIC_BOARD_AUTOMAIL_JS,
    _STATIC_BOARD_CSS,
    _STATIC_BOARD_JS,
    GLOBAL_VIEW_ACCOUNT_ID,
    _is_safe_redirect_path,
    _parse_archive_structure,
)
from robotsix_auto_mail.server.adapters import (
    _batch_op_running,
    _collect_records_for_action,
    _release_batch_op,
    _run_batch_archive_background,
    _run_batch_delete_background,
    _run_folder_triage_background,
    _run_triage_background,
)
from robotsix_auto_mail.server.views import (
    _build_board_content,
    _build_board_html,
    _build_detail_html,
    _build_global_board_content,
    _build_global_board_html,
)
from robotsix_auto_mail.triage import (
    VALID_TRIAGE_ACTIONS,
    TriageError,
    get_archive_subfolder,
    get_triage_decision,
    propose_archive_subfolder_llm,
    record_archive_folder_choice,
    record_human_decision,
    set_archive_subfolder_override,
    set_rule_state,
    set_triage_decision,
)


def _compute_reply_all_cc(record: MailRecord, from_addr: str) -> list[str] | None:
    """Compute the CC list for a reply-all, excluding self and the original sender."""
    try:
        recipients = json.loads(record.recipients_json)
    except (json.JSONDecodeError, TypeError):
        recipients = {}
    orig_to = recipients.get("to", []) if isinstance(recipients, dict) else []
    orig_cc = recipients.get("cc", []) if isinstance(recipients, dict) else []
    to_addr = record.sender
    cc_list: list[str] = []
    seen: set[str] = set()
    excluded = {from_addr.lower(), to_addr.lower()}
    for addr in [*orig_to, *orig_cc]:
        if not isinstance(addr, str):
            continue
        lowered = addr.lower()
        if lowered in excluded or lowered in seen:
            continue
        seen.add(lowered)
        cc_list.append(addr)
    return cc_list or None


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

    def _serve_board(self) -> None:
        """Render and serve the kanban board HTML."""
        if self._aggregate and self.accounts is not None:
            try:
                body = _build_global_board_html(self.accounts)
            except Exception:
                self._send_response("Database unavailable", status=503)
                return
            self._send_response(body, content_type="text/html; charset=utf-8")
            return

        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )
        try:
            body = _build_board_html(
                self.db_path,
                archive_root=archive_root,
                accounts=self.accounts,
                current_account_id=self._current_account_id,
            )
        except Exception:
            self._send_response("Database unavailable", status=503)
            return

        self._send_response(body, content_type="text/html; charset=utf-8")

    def _serve_board_content(self) -> None:
        """Render and serve the board content as JSON."""
        if self._aggregate and self.accounts is not None:
            try:
                payload = _build_global_board_content(self.accounts)
            except Exception:
                self._serve_json({"error": "Database unavailable"}, status=503)
                return
            self._serve_json(payload)
            return

        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )
        try:
            payload = _build_board_content(self.db_path, archive_root=archive_root)
        except Exception:
            self._serve_json({"error": "Database unavailable"}, status=503)
            return

        self._serve_json(payload)

    def _serve_static(self) -> None:
        """Serve static assets from the robotsix_board package."""
        if self.path == "/static/board.js":
            self._send_response(
                _STATIC_BOARD_JS,
                content_type="text/javascript; charset=utf-8",
            )
        elif self.path == "/static/board.css":
            self._send_response(
                _STATIC_BOARD_CSS,
                content_type="text/css; charset=utf-8",
            )
        elif self.path == "/static/automail/board.css":
            self._send_response(
                _STATIC_AUTOMAIL_BOARD_CSS,
                content_type="text/css; charset=utf-8",
            )
        elif self.path == "/static/board-auto-mail.js":
            self._send_response(
                _STATIC_BOARD_AUTOMAIL_JS,
                content_type="text/javascript; charset=utf-8",
            )
        else:
            self._not_found()

    def _serve_folders(self) -> None:
        """Serve GET /folders — list IMAP mailbox folders as JSON.

        Folder enumeration is deliberately served from this async
        endpoint (not during the synchronous ``/board`` render) so a slow
        or unreachable IMAP server never blocks the single-threaded board
        page.  Returns 503 when IMAP is unconfigured and 502 on an
        ``ImapError``.
        """
        if self.mail_config is None:
            self._send_response(
                json.dumps({"error": "IMAP not configured"}).encode(),
                status=503,
                content_type="application/json",
            )
            return

        from robotsix_auto_mail.imap import ImapClient, ImapError

        try:
            with ImapClient(self.mail_config) as client:
                folders = [info.name for info in client.list_folders()]
        except ImapError as exc:
            self._send_response(
                json.dumps({"error": str(exc)}).encode(),
                status=502,
                content_type="application/json",
            )
            return

        self._send_response(
            json.dumps({"folders": folders}).encode(),
            status=200,
            content_type="application/json",
        )

    def _handle_move(self) -> None:
        """Process POST /move — update a card's triage decision and redirect."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        # parse_qs returns {key: [value, ...]} — extract first value.
        message_id = (fields.get("message_id") or [""])[0].strip()
        triage_action = (fields.get("triage_action") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id or not triage_action:
            self._bad_request("Missing message_id or triage_action")
            return

        if triage_action not in VALID_TRIAGE_ACTIONS:
            self._bad_request(f"Invalid triage action: {triage_action!r}")
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            # Verify the record exists before upserting a triage decision
            # (foreign key would reject it anyway, but we want a clean 404).
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return
            set_triage_decision(
                conn,
                message_id,
                triage_action,
                source="user",
                reason=f"moved to {triage_action}",
            )
            record_human_decision(conn, message_id, triage_action)

            if triage_action == "TO_ARCHIVE":
                try:
                    if record is not None and self.mail_config is not None:
                        propose_archive_subfolder_llm(
                            conn,
                            record,
                            self.mail_config.llm_api_key,
                            provider=(
                                self.mail_config.llm_provider
                                if self.mail_config
                                else None
                            ),
                        )
                except Exception:  # noqa: S110  # nosec B110
                    pass  # Non-fatal: board falls back to deterministic proposal
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_delete(self) -> None:
        """Process POST /delete — delete mail from IMAP mailbox and local DB."""
        from robotsix_auto_mail.db import (
            delete_record_by_message_id,
            get_record_by_message_id,
            init_db,
        )

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            # -- IMAP deletion (when config and UID are both available) --
            if self.mail_config is not None and record.imap_uid is not None:
                from robotsix_auto_mail.imap import (
                    ImapClient,
                    ImapError,
                    ImapMessageNotFoundError,
                    resolve_uid_with_fallback,
                )

                try:
                    with ImapClient(self.mail_config) as client:
                        resolved_uid = resolve_uid_with_fallback(
                            client,
                            record.source_folder,
                            record.imap_uid,
                            record.message_id,
                        )
                        client.delete_message(resolved_uid)
                except ImapMessageNotFoundError as exc:
                    folder_label = record.source_folder or "its source folder"
                    self._send_response(
                        f"Message {message_id} is no longer in "
                        f"{folder_label} — the tracked UID is stale, "
                        f"so it was not deleted and the board record "
                        f"was kept: {exc}",
                        status=409,
                    )
                    return
                except (ImapError, OSError) as exc:
                    self._send_response(
                        f"IMAP deletion failed: {exc}",
                        status=502,
                    )
                    return

            # -- local DB deletion --
            delete_record_by_message_id(conn, message_id)
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _imap_archive_move(
        self,
        mail_config: MailConfig,
        imap_uid: int,
        effective_root: str,
        subfolder: str | None,
        source_folder: str = "INBOX",
        message_id: str = "",
    ) -> None:
        """Move a message to the archive folder via IMAP.

        Selects *source_folder* (the record's origin folder) rather
        than assuming ``config.imap_folder``.  If the stored UID is
        stale, falls back to a ``HEADER Message-ID`` search before
        giving up.

        Raises ValueError on security-policy violations (caller should
        return 400).  Raises ImapError or OSError on IMAP/IO failures
        (caller should return 502).
        """
        from robotsix_auto_mail.imap import ImapClient, resolve_uid_with_fallback

        with ImapClient(mail_config) as client:
            # Resolve the possibly-stale UID, selecting source_folder.
            resolved_uid = resolve_uid_with_fallback(
                client, source_folder, imap_uid, message_id
            )

            # Determine the IMAP hierarchy delimiter.
            existing = client.list_folders()
            delimiter = next(
                (f.delimiter for f in existing if f.delimiter),
                "/",
            )

            # Build the destination IMAP folder name.
            if subfolder:
                translated = subfolder.replace("/", delimiter)
                dest_folder = f"{effective_root}{delimiter}{translated}"
            else:
                dest_folder = effective_root

            # -- security gate ---------------------------------
            # Reject any destination that escapes the archive
            # root (must start with root+delimiter or equal the
            # root itself) and forbid ".." path segments.
            root_prefix = f"{effective_root}{delimiter}"
            if dest_folder != effective_root and not dest_folder.startswith(
                root_prefix
            ):
                raise ValueError("Archive destination escapes archive root")
            if ".." in dest_folder.split(delimiter):
                raise ValueError("Archive destination contains '..' path segment")

            # -- ensure destination folder hierarchy exists ----
            parts = dest_folder.split(delimiter)
            for i in range(1, len(parts) + 1):
                client.create_folder(delimiter.join(parts[:i]))

            client.move_message(resolved_uid, dest_folder)

    def _archive_and_delete(self, conn: Any, record: MailRecord) -> bool:
        """Archive *record*'s message via IMAP, then delete its local row.

        Shared by :meth:`_handle_archive` and :meth:`_handle_send_draft`.
        Computes the effective archive root + subfolder, performs the IMAP
        move (only when IMAP is configured and the record has a tracked
        UID), then removes the local database record.

        Returns ``True`` on success.  On a security-policy violation it
        sends a 400 and returns ``False``; on an IMAP/IO failure it sends a
        502 and returns ``False`` — in both error cases the local record is
        left intact.
        """
        from robotsix_auto_mail.db import delete_record_by_message_id

        # Compute the effective archive subfolder.
        subfolder = get_archive_subfolder(conn, record.message_id, record)

        # Determine the archive root.
        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

        # Determine the namespace prefix (empty when unset).
        namespace = (
            self.mail_config.archive_namespace if self.mail_config is not None else ""
        )

        # Effective root: namespace + archive_root (user supplies
        # the delimiter as part of the namespace, e.g. "INBOX.").
        effective_root = namespace + archive_root

        # -- IMAP move phase (only when IMAP is configured and the
        #    record has a tracked UID) --
        if self.mail_config is not None and record.imap_uid is not None:
            from robotsix_auto_mail.imap import ImapError, ImapMessageNotFoundError

            try:
                self._imap_archive_move(
                    self.mail_config,
                    record.imap_uid,
                    effective_root,
                    subfolder,
                    source_folder=record.source_folder,
                    message_id=record.message_id,
                )
            except ValueError as exc:
                self._bad_request(str(exc))
                return False
            except ImapMessageNotFoundError as exc:
                folder_label = record.source_folder or "its source folder"
                self._send_response(
                    f"Message {record.message_id} is no longer in "
                    f"{folder_label} — the tracked UID is stale, so "
                    f"it was not archived and the board record was "
                    f"kept: {exc}",
                    status=409,
                )
                return False
            except (ImapError, OSError) as exc:
                self._send_response(
                    f"IMAP archive failed: {exc}",
                    status=502,
                )
                return False

        # -- record the human-confirmed archive-folder choice (best-effort),
        #    BEFORE the local row is deleted so the memory survives --
        if subfolder:
            try:
                record_archive_folder_choice(conn, record, subfolder)
            except Exception:  # noqa: S110  # nosec B110
                pass  # Non-fatal: memory is advisory only

        # -- local DB cleanup --
        delete_record_by_message_id(conn, record.message_id)
        return True

    def _handle_archive(self) -> None:
        """Process POST /archive — move mail to archive folder via IMAP
        and remove it from the local database.
        """
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            if not self._archive_and_delete(conn, record):
                return
        finally:
            conn.close()

        self._redirect("/board", code=302)

    def _handle_batch_delete(self) -> None:
        """Process POST /batch-delete — delete all TO_DELETE mail from IMAP
        and local DB in a background daemon thread.

        Single-flight guarded by the shared ``batch_op:state`` watermark
        (so delete and archive cannot run concurrently on the same
        account).  Before handing off to the background worker, a
        **synchronous stale-UID precheck** verifies that every tracked
        UID still exists in the selected IMAP folder.  If any UID is
        stale the handler responds with **409** and nothing is deleted
        — mirroring the single-delete path in :meth:`_handle_delete`.
        """
        import threading

        from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if _batch_op_running(get_watermark(conn, "batch_op:state")):
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "batch_op:state", "running")
        finally:
            conn.close()

        # -- synchronous stale-UID precheck (before redirect) --
        if self.mail_config is not None:
            from robotsix_auto_mail.imap import (
                ImapClient,
                ImapError,
                ImapMessageNotFoundError,
                resolve_uid_with_fallback,
            )

            conn = init_db(self.db_path, skip_migrations=True)
            try:
                records = _collect_records_for_action(conn, "TO_DELETE")
            finally:
                conn.close()

            if any(r.imap_uid is not None for r in records):
                try:
                    with ImapClient(self.mail_config) as client:
                        for record in records:
                            if record.imap_uid is None:
                                continue
                            resolve_uid_with_fallback(
                                client,
                                record.source_folder,
                                record.imap_uid,
                                record.message_id,
                            )
                except ImapMessageNotFoundError as exc:
                    _release_batch_op(self.db_path)
                    self._send_response(
                        f"Batch delete aborted — a tracked UID is stale, "
                        f"so no messages were deleted: {exc}",
                        status=409,
                    )
                    return
                except (ImapError, OSError) as exc:
                    _release_batch_op(self.db_path)
                    self._send_response(
                        f"IMAP precheck failed: {exc}",
                        status=502,
                    )
                    return

        threading.Thread(
            target=_run_batch_delete_background,
            args=(self.db_path, self.mail_config),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)

    def _handle_batch_archive(self) -> None:
        """Process POST /batch-archive — archive all TO_ARCHIVE mail from
        IMAP and local DB in a background daemon thread.

        Single-flight guarded by the shared ``batch_op:state`` watermark
        (so delete and archive cannot run concurrently on the same
        account).  Before handing off to the background worker, a
        **synchronous stale-UID precheck** verifies that every tracked
        UID still exists in the selected IMAP folder.  If any UID is
        stale the handler responds with **409** and nothing is archived
        — mirroring the single-archive path.  The redirect is returned
        after the precheck passes; the worker groups UIDs by destination
        folder and batch-moves each group.
        """
        import threading

        from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if _batch_op_running(get_watermark(conn, "batch_op:state")):
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "batch_op:state", "running")
        finally:
            conn.close()

        # -- synchronous stale-UID precheck (before redirect) --
        if self.mail_config is not None:
            from robotsix_auto_mail.imap import (
                ImapClient,
                ImapError,
                ImapMessageNotFoundError,
                resolve_uid_with_fallback,
            )

            conn = init_db(self.db_path, skip_migrations=True)
            try:
                records = _collect_records_for_action(conn, "TO_ARCHIVE")
            finally:
                conn.close()

            if any(r.imap_uid is not None for r in records):
                try:
                    with ImapClient(self.mail_config) as client:
                        for record in records:
                            if record.imap_uid is None:
                                continue
                            resolve_uid_with_fallback(
                                client,
                                record.source_folder,
                                record.imap_uid,
                                record.message_id,
                            )
                except ImapMessageNotFoundError as exc:
                    _release_batch_op(self.db_path)
                    self._send_response(
                        f"Batch archive aborted — a tracked UID is stale, "
                        f"so no messages were archived: {exc}",
                        status=409,
                    )
                    return
                except (ImapError, OSError) as exc:
                    _release_batch_op(self.db_path)
                    self._send_response(
                        f"IMAP precheck failed: {exc}",
                        status=502,
                    )
                    return

        threading.Thread(
            target=_run_batch_archive_background,
            args=(self.db_path, self.mail_config, archive_root),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)

    def _handle_rule_action(self) -> None:
        """Process POST /rule-action — accept/reject a rule proposal."""
        from robotsix_auto_mail.db import init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        # parse_qs returns {key: [value, ...]} — extract first value.
        fingerprint = (fields.get("fingerprint") or [""])[0].strip()
        decision = (fields.get("decision") or [""])[0].strip()

        if not fingerprint or not decision:
            self._bad_request("Missing fingerprint or decision")
            return

        decision_to_state = {"accept": "accepted", "reject": "rejected"}
        mapped_state = decision_to_state.get(decision)
        if mapped_state is None:
            self._bad_request(f"Invalid decision: {decision!r}")
            return

        conn = init_db(self.db_path)
        try:
            set_rule_state(conn, fingerprint, mapped_state)
        except TriageError:
            self._not_found()
            return
        finally:
            conn.close()

        self._redirect("/board", code=302)

    def _handle_config_sync(self) -> None:
        """Process POST /config-sync — run the LLM drift advisory agent.

        Lazily imports the optional LLM-backed agent so the rest of the
        server works without ``pydantic_ai`` installed.  On success,
        returns the ``ConfigSyncResult`` serialized as JSON; on a missing
        optional extra returns 503, and on any agent failure returns 503
        with a JSON error body (never a traceback).
        """
        try:
            from robotsix_auto_mail.config.config_sync_agent import (
                ConfigSyncError,
                run_config_sync_agent,
            )
        except ImportError:
            self._serve_json(
                {
                    "error": (
                        "Config-sync advisory requires the optional LLM "
                        "extra, which is not installed"
                    )
                },
                status=503,
            )
            return

        from robotsix_auto_mail.db import init_db

        conn = init_db(self.db_path)
        try:
            result = run_config_sync_agent(conn=conn)
        except ConfigSyncError as exc:
            self._serve_json({"error": str(exc)}, status=503)
            return
        except Exception as exc:
            self._serve_json({"error": str(exc)}, status=503)
            return
        finally:
            conn.close()

        self._serve_json(result.model_dump(), status=200)

    def _handle_run_triage(self) -> None:
        """Process POST /run-triage — launch triage agent in a background thread.

        Idempotent: if triage is already running the request is a no-op
        that redirects to ``/board`` immediately.  Otherwise a watermark
        is set and a daemon thread is spawned to run the agent; the
        thread clears the watermark in a ``finally`` block so the board
        always recovers.
        """
        import threading

        from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_watermark(conn, "triage_run:state") == "running":
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "triage_run:state", "running")
        finally:
            conn.close()

        threading.Thread(
            target=_run_triage_background,
            args=(
                self.db_path,
                self.mail_config.username if self.mail_config is not None else None,
            ),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)

    def _handle_run_folder_triage(self) -> None:
        """Process POST /run-folder-triage — one-shot triage over a folder.

        Mirrors :meth:`_handle_run_triage` but ingests a named IMAP
        folder (supplied per-request via the ``folder`` param) before
        running the triage agent.  Requires IMAP to be configured;
        validates the ``folder`` param; guards on the shared
        ``triage_run:state`` watermark (idempotent when already running);
        spawns a daemon thread and redirects to ``/board``.
        """
        import threading
        import urllib.parse

        from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b""
        params = urllib.parse.parse_qs(raw_body.decode("utf-8"))
        folder_list = params.get("folder", [])
        if not folder_list or not folder_list[0].strip():
            self._bad_request("Missing 'folder' parameter")
            return
        folder = folder_list[0].strip()

        if self.mail_config is None:
            self._bad_request("Folder triage requires IMAP configuration")
            return
        mail_config = self.mail_config

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_watermark(conn, "triage_run:state") == "running":
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "triage_run:state", "running")
        finally:
            conn.close()

        threading.Thread(
            target=_run_folder_triage_background,
            args=(self.db_path, mail_config, folder),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)

    def _handle_force_triage_column(self) -> None:
        """Process POST /force-triage-column — reset triage decisions for
        one column, then launch the triage agent in a background thread.

        Follows the same pattern as :meth:`_handle_run_triage`: decisions
        are deleted, then the global agent is spawned (or joined if
        already running).  The watermark guard ensures only one triage
        run is in flight at a time.
        """
        import threading
        import urllib.parse

        from robotsix_auto_mail.db import (
            VALID_TRIAGE_ACTIONS,
            get_watermark,
            init_db,
            set_watermark,
        )
        from robotsix_auto_mail.triage import (
            TriageError,
            delete_triage_decisions_by_action,
        )

        # -- parse body ---------------------------------------------------
        content_length = int(self.headers.get("content-length", "0"))
        raw_body = self.rfile.read(content_length) if content_length else b""
        params = urllib.parse.parse_qs(raw_body.decode("utf-8"))
        action_list = params.get("action", [])
        if not action_list or not action_list[0].strip():
            self._bad_request("Missing 'action' parameter")
            return
        action = action_list[0].strip()
        if action not in VALID_TRIAGE_ACTIONS:
            self._bad_request(f"Invalid triage action: {action!r}")
            return

        # -- clear decisions ----------------------------------------------
        try:
            conn = init_db(self.db_path, skip_migrations=True)
            try:
                delete_triage_decisions_by_action(conn, action)
            finally:
                conn.close()
        except TriageError as exc:
            self._bad_request(str(exc))
            return
        except Exception as exc:
            self._send_response(
                json.dumps({"error": str(exc)}).encode(),
                status=503,
                content_type="application/json",
            )
            return

        # -- launch triage (same pattern as _handle_run_triage) -----------
        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_watermark(conn, "triage_run:state") == "running":
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "triage_run:state", "running")
        finally:
            conn.close()

        threading.Thread(
            target=_run_triage_background,
            args=(
                self.db_path,
                self.mail_config.username if self.mail_config is not None else None,
            ),
            daemon=True,
        ).start()

        self._redirect("/board", code=302)

    def _serve_archive_proposal(self) -> None:
        """Serve GET /archive-proposal/{message_id} — return JSON with
        effective subfolder, source, and folder-exists status."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            get_watermark,
            init_db,
        )
        from robotsix_auto_mail.triage import (
            _load_archive_overrides,
            _load_llm_archive_hints,
        )

        path = self.path
        prefix = "/archive-proposal/"
        message_id = unquote(path[len(prefix) :])

        archive_root = (
            self.mail_config.archive_root
            if self.mail_config is not None
            else DEFAULT_ARCHIVE_ROOT
        )

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            subfolder = get_archive_subfolder(conn, message_id, record)
            overrides = _load_archive_overrides(conn)
            hints = _load_llm_archive_hints(conn)

            if message_id in overrides:
                source = "override"
                overridden = True
            elif message_id in hints:
                source = "llm"
                overridden = False
            else:
                source = "rule"
                overridden = False

            # Determine folder_exists
            archive_raw = get_watermark(conn, "archive_structure")
            existing_folders, delimiter, effective_root = _parse_archive_structure(
                archive_raw, archive_root
            )
            if subfolder:
                translated = subfolder.replace("/", delimiter)
                full_path = f"{effective_root}{delimiter}{translated}"
            else:
                full_path = effective_root
            folder_exists = full_path in existing_folders
        finally:
            conn.close()

        self._serve_json(
            {
                "subfolder": subfolder,
                "archive_root": archive_root,
                "folder_exists": folder_exists,
                "overridden": overridden,
                "source": source,
            }
        )

    def _handle_archive_proposal(self) -> None:
        """Process POST /archive-proposal — store a user override and redirect."""
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        subfolder = (fields.get("subfolder") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        if subfolder:
            if subfolder.startswith("/"):
                self._bad_request("Subfolder must not be an absolute path")
                return
            if any(segment == ".." for segment in subfolder.split("/")):
                self._bad_request("Subfolder must not contain '..' segments")
                return
            if len(subfolder) > 256:
                self._bad_request("Subfolder exceeds maximum length of 256 characters")
                return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            set_archive_subfolder_override(conn, message_id, subfolder)
            # -- record the human-confirmed folder choice (best-effort);
            #    an empty subfolder (clearing the override) records nothing --
            if subfolder:
                try:
                    record = get_record_by_message_id(conn, message_id)
                    if record is not None:
                        record_archive_folder_choice(conn, record, subfolder)
                except Exception:  # noqa: S110  # nosec B110
                    pass  # Non-fatal: memory is advisory only
        finally:
            conn.close()

        self._redirect("/board", code=302)

    def _handle_save_notes(self) -> None:
        """Process POST /save-notes — persist notes for a mail record."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            init_db,
            update_notes,
        )

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        notes = (fields.get("notes") or [""])[0]
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        # Verify the record exists (read-only check).
        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_record_by_message_id(conn, message_id) is None:
                self._not_found()
                return
        finally:
            conn.close()

        # Persist the notes.
        conn = init_db(self.db_path)
        try:
            update_notes(conn, message_id, notes)
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_save_draft(self) -> None:
        """Process POST /save-draft — persist draft text and move to DRAFT_READY."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            init_db,
            update_draft_text,
        )

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        draft_text = (fields.get("draft_text") or [""])[0]
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        # Verify the record exists (read-only check).
        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_record_by_message_id(conn, message_id) is None:
                self._not_found()
                return
        finally:
            conn.close()

        # Persist draft text and move to DRAFT_READY.
        conn = init_db(self.db_path)
        try:
            update_draft_text(conn, message_id, draft_text)

            current = get_triage_decision(conn, message_id)
            if current is None or current.action != "DRAFT_READY":
                set_triage_decision(
                    conn,
                    message_id,
                    "DRAFT_READY",
                    source="user",
                    reason="draft saved",
                )
                record_human_decision(conn, message_id, "DRAFT_READY")
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_send_draft(self) -> None:
        """Process POST /send-draft — send the saved draft via SMTP, then
        re-queue the original message for triage.

        Mirrors :meth:`_handle_save_draft` for form parsing/validation.
        After a successful send the original record is **not** archived;
        instead its sent reply body is persisted and its triage decision is
        cleared so the email re-enters the untriaged pool and the triage
        agent owns the post-answer disposition.
        """
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            init_db,
            update_sent_reply_text,
        )
        from robotsix_auto_mail.triage import delete_triage_decision

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        reply_mode = (fields.get("reply_mode") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        # Validate reply mode up-front (cheap, no DB access).
        if reply_mode not in ("reply", "reply_all"):
            self._bad_request(f"Invalid reply_mode: {reply_mode!r}")
            return

        # SMTP must be configured to send anything.
        if self.mail_config is None or not self.mail_config.smtp_host:
            self._bad_request("SMTP is not configured")
            return
        mail_config = self.mail_config

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return

            if not record.draft_text.strip():
                self._bad_request("Draft is empty; nothing to send")
                return

            # -- compute recipients ------------------------------------
            from_addr = mail_config.username
            to_addr = record.sender

            # Defensive guard: never reply to the user's own address
            # (a self-sent message that slipped through triage).
            if to_addr.strip().lower() == from_addr.strip().lower():
                self._bad_request("Refusing to send a reply to your own address")
                return

            cc = (
                _compute_reply_all_cc(record, from_addr)
                if reply_mode == "reply_all"
                else None
            )

            # -- subject (prepend "Re: " unless already present) -------
            subject = record.subject
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"

            # -- send via SMTP -----------------------------------------
            from robotsix_auto_mail.smtp import SmtpClient

            with SmtpClient(mail_config) as client:
                client.send(
                    from_addr=from_addr,
                    to_addr=to_addr,
                    subject=subject,
                    body=record.draft_text,
                    cc=cc,
                    in_reply_to=record.message_id,
                    references=record.message_id,
                )

            # -- re-queue for triage: persist the sent reply and drop the
            #    existing triage decision so the record re-enters the
            #    untriaged pool (no archive move, no record deletion) ----
            update_sent_reply_text(conn, record.message_id, record.draft_text)
            delete_triage_decision(conn, record.message_id)
        finally:
            conn.close()

        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, code=302)
        else:
            self._redirect("/board", code=302)

    def _handle_generate_draft(self) -> None:
        """Process POST /generate-draft — LLM-generate a draft reply.

        Lazily imports the optional LLM-backed draft generator so the rest
        of the server works without ``pydantic_ai`` installed.  On a missing
        optional extra (``ImportError``) the handler degrades gracefully by
        redirecting back to the detail/board view (the manual textarea stays
        usable) rather than returning a 503 — a full-page POST cannot render
        a clean JSON error.  Generation failures are likewise swallowed so
        the existing draft/manual form remains available.
        """
        from robotsix_auto_mail.db import init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        message_id = (fields.get("message_id") or [""])[0].strip()
        redirect_to = (fields.get("redirect_to") or [""])[0].strip()

        if not message_id:
            self._bad_request("Missing message_id")
            return

        try:
            from robotsix_auto_mail.draft import (
                DraftGenerationError,
                generate_draft_reply,
            )
        except ImportError:
            # Optional LLM extra not installed — degrade silently.
            self._redirect_generate_draft(message_id, redirect_to)
            return

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            try:
                generate_draft_reply(
                    conn,
                    message_id,
                    api_key=(
                        self.mail_config.llm_api_key if self.mail_config else None
                    ),
                    provider=(
                        self.mail_config.llm_provider if self.mail_config else None
                    ),
                )
            except DraftGenerationError:
                # Generation failed — degrade gracefully (existing draft /
                # manual form stays); fall through to the redirect.
                pass
            else:
                set_triage_decision(
                    conn,
                    message_id,
                    "DRAFT_READY",
                    source="user",
                    reason="draft generated",
                )
        finally:
            conn.close()

        self._redirect_generate_draft(message_id, redirect_to)

    def _redirect_generate_draft(self, message_id: str, redirect_to: str) -> None:
        """Redirect after /generate-draft to *redirect_to* or the board panel.

        When *redirect_to* is a safe relative path it is used (returning the
        iframe to the embed detail view).  Otherwise a server-side trusted
        ``/board#{message_id}`` redirect re-opens the side panel on the now
        ``DRAFT_READY`` card.
        """
        if redirect_to and _is_safe_redirect_path(redirect_to):
            self._redirect(redirect_to, 302)
        else:
            self._redirect(f"/board#{quote(message_id)}", 302)

    def _serve_email_status(self) -> None:
        """Serve GET /email/{message_id}/status — return triage action as text.

        Returns ``"INBOX"`` when the record exists but has no triage
        decision.  Returns 404 when the message_id is unknown.
        """
        from robotsix_auto_mail.db import get_record_by_message_id, init_db

        # Extract the URL-encoded message_id from the path:
        #   "/email/<encoded>/status" → extract and decode.
        path = self.path
        prefix = "/email/"
        suffix = "/status"
        encoded_mid = path[len(prefix) : -len(suffix)]
        message_id = unquote(encoded_mid)

        conn = init_db(self.db_path)
        try:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                self._not_found()
                return
            decision = get_triage_decision(conn, message_id)
        finally:
            conn.close()

        if decision is None:
            self._send_response("INBOX")
            return

        self._send_response(decision.action)

    def _serve_email_detail(self) -> None:
        """Serve GET /email/{message_id} — full detail page.

        Supports ``?embed=1`` to return a fragment suitable for an
        iframe (no full-page chrome, no refresh).
        """
        from urllib.parse import parse_qs, urlparse

        path = self.path
        prefix = "/email/"

        # Separate path from query string.
        parsed = urlparse(path)
        message_id = unquote(parsed.path[len(prefix) :])
        qs = parse_qs(parsed.query)
        embed = qs.get("embed", ["0"])[0] == "1"
        focus_draft = qs.get("draft", ["0"])[0] == "1"

        try:
            detail_html = _build_detail_html(
                self.db_path,
                message_id,
                embed=embed,
                focus_draft=focus_draft,
            )
        except Exception:
            self._send_response("Database unavailable", status=503)
            return

        if detail_html is None:
            self._not_found()
            return

        self._send_response(detail_html, content_type="text/html; charset=utf-8")

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
