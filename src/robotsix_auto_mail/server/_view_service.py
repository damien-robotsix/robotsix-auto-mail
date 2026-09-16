"""View-serving service for the board server.

Stateless composition-era replacement for the legacy ``_BoardViewMixin``.
Every endpoint method takes the per-request context
(:class:`robotsix_auto_mail.server._board_handler_protocol.RequestContext`)
and reads all per-request state (``_current_account_id``, ``_aggregate``,
``path``, the response sinks, ``_effective_archive_root``) off it — the
service instance itself holds only the injected dependencies and is safe to
share across every request.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast
from urllib.parse import unquote

from robotsix_auto_mail.config import resolve_llm_api_key
from robotsix_auto_mail.server._constants import (
    _STATIC_ADD_ACCOUNT_REDIRECT_JS,
    _STATIC_APPSHELL_LOADER_JS,
    _STATIC_AUTOMAIL_BOARD_CSS,
    _STATIC_BOARD_AUTOMAIL_JS,
    _STATIC_BOARD_CSS,
    _STATIC_BOARD_EVENTS_JS,
    _STATIC_BOARD_JS,
    _STATIC_ROBOTSIX_UI_CSS,
    _STATIC_ROBOTSIX_UI_JS,
    _STATIC_SETTINGS_LOADER_JS,
    _parse_archive_structure,
    _with_db,
)
from robotsix_auto_mail.server._services import Service
from robotsix_auto_mail.server.views import (
    _build_board_content,
    _build_board_html,
    _build_detail_html,
    _build_global_board_content,
    _build_global_board_html,
)
from robotsix_auto_mail.triage import (
    INBOX,
    get_archive_subfolder,
    get_triage_decision,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailAccountsConfig, MailConfig
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext

logger = logging.getLogger(__name__)


class ViewService(Service):
    """Stateless service providing the board's GET view/rendering endpoints."""

    def serve_board(self, ctx: RequestContext) -> None:
        """Render and serve the kanban board HTML."""
        if ctx._aggregate and ctx.accounts is not None:
            try:
                body = _build_global_board_html(
                    cast("MailAccountsConfig", ctx.accounts)
                )
            except Exception:
                ctx._send_response("Database unavailable", status=503)
                return
            ctx._send_response(body, content_type="text/html; charset=utf-8")
            return

        archive_root = ctx._effective_archive_root
        try:
            body = _build_board_html(
                ctx.db_path,
                archive_root=archive_root,
                accounts=cast("MailAccountsConfig | None", ctx.accounts),
                current_account_id=ctx._current_account_id,
            )
        except Exception:
            ctx._send_response("Database unavailable", status=503)
            return

        ctx._send_response(body, content_type="text/html; charset=utf-8")

    def serve_board_content(self, ctx: RequestContext) -> None:
        """Render and serve the board content as JSON.

        Supports ``?format=json`` for structured card data (no HTML).
        When ``format=json`` is present:
        - Omitted ``?account=`` → all configured accounts.
        - ``?account=<id>`` → single account; unknown id → 404.
        """
        from urllib.parse import parse_qs, urlsplit

        qs = parse_qs(urlsplit(ctx.path).query)

        # -- structured JSON mode ---------------------------------------
        if qs.get("format") == ["json"]:
            # Omitted ?account= → aggregate (all accounts).
            if (
                "account" not in qs
                and ctx.accounts is not None
                and cast("MailAccountsConfig", ctx.accounts).ids()
            ):
                try:
                    payload = self._build_board_json_aggregate(ctx)
                except Exception:
                    ctx._serve_json({"error": "Database unavailable"}, status=503)
                    return
                ctx._serve_json(payload)
                return

            if ctx._aggregate and ctx.accounts is not None:
                try:
                    payload = self._build_board_json_aggregate(ctx)
                except Exception:
                    ctx._serve_json({"error": "Database unavailable"}, status=503)
                    return
                ctx._serve_json(payload)
                return

            # Single account (already resolved by _select_account).
            archive_root = ctx._effective_archive_root
            try:
                payload = self._build_board_json_single(
                    ctx.db_path,
                    archive_root,
                    account_id=ctx._current_account_id or "main",
                )
            except Exception:
                ctx._serve_json({"error": "Database unavailable"}, status=503)
                return
            ctx._serve_json(payload)
            return

        # -- existing HTML mode -----------------------------------------
        if ctx._aggregate and ctx.accounts is not None:
            try:
                payload = _build_global_board_content(
                    cast("MailAccountsConfig", ctx.accounts)
                )
            except Exception:
                ctx._serve_json({"error": "Database unavailable"}, status=503)
                return
            ctx._serve_json(payload)
            return

        archive_root = ctx._effective_archive_root
        try:
            payload = _build_board_content(
                ctx.db_path,
                archive_root=archive_root,
                account_id=ctx._current_account_id or "main",
                config_failures=(),
                mail_config=cast("MailConfig | None", ctx.mail_config),
            )
        except Exception:
            ctx._serve_json({"error": "Database unavailable"}, status=503)
            return

        ctx._serve_json(payload)

    # -- JSON board helpers -----------------------------------------------

    def _build_board_json_single(
        self, db_path: str, archive_root: str, *, account_id: str
    ) -> dict[str, object]:
        """Build structured JSON board content for a single account.

        Returns ``{"columns": {<action>: [cards…], …}, "triage_running": bool}``.
        Each card has ``message_id``, ``subject``, ``from``, ``date``,
        ``status`` (triage column), and ``account``.
        """
        from robotsix_auto_mail.server.views.board_data import (
            _gather_account_board_data,
        )

        gathered = _gather_account_board_data(db_path, archive_root=archive_root)
        column_buckets = gathered["column_buckets"]
        triage_running: bool = gathered["triage_running"]

        columns: dict[str, list[dict[str, object]]] = {}
        for column, records in column_buckets.items():
            if not records:
                continue
            cards: list[dict[str, object]] = []
            for record in records:
                cards.append(
                    {
                        "message_id": record.message_id,
                        "subject": record.subject,
                        "from": record.sender,
                        "date": record.date,
                        "status": column,
                        "account": account_id,
                    }
                )
            columns[column] = cards

        return {"columns": columns, "triage_running": triage_running}

    def _build_board_json_aggregate(self, ctx: RequestContext) -> dict[str, object]:
        """Build structured JSON board content for all configured accounts.

        Merges per-account column buckets; each card carries its owning
        ``account`` id.
        """
        from robotsix_auto_mail.server.views.board_data import (
            _gather_account_board_data,
        )

        if ctx.accounts is None:
            return {}
        accounts = cast("MailAccountsConfig", ctx.accounts)

        merged_columns: dict[str, list[dict[str, object]]] = {}
        triage_running = False

        for account in accounts.accounts:
            aid = account.account_id
            try:
                gathered = _gather_account_board_data(
                    account.config.db_path,
                    archive_root=account.config.archive_root,
                )
            except Exception:
                logger.debug(
                    "Could not gather board data for account %s",
                    aid,
                    exc_info=True,
                )
                continue

            triage_running = triage_running or gathered["triage_running"]
            for column, records in gathered["column_buckets"].items():
                if column not in merged_columns:
                    merged_columns[column] = []
                for record in records:
                    merged_columns[column].append(
                        {
                            "message_id": record.message_id,
                            "subject": record.subject,
                            "from": record.sender,
                            "date": record.date,
                            "status": column,
                            "account": aid,
                        }
                    )

        return {"columns": merged_columns, "triage_running": triage_running}

    def serve_static(self, ctx: RequestContext) -> None:
        """Serve static assets from the robotsix_board package."""
        if ctx.path == "/static/board.js":
            ctx._send_response(
                _STATIC_BOARD_JS,
                content_type="text/javascript; charset=utf-8",
            )
        elif ctx.path == "/static/board.css":
            ctx._send_response(
                _STATIC_BOARD_CSS,
                content_type="text/css; charset=utf-8",
            )
        elif ctx.path == "/static/automail/board.css":
            ctx._send_response(
                _STATIC_AUTOMAIL_BOARD_CSS,
                content_type="text/css; charset=utf-8",
            )
        elif ctx.path == "/static/board-auto-mail.js":
            ctx._send_response(
                _STATIC_BOARD_AUTOMAIL_JS,
                content_type="text/javascript; charset=utf-8",
            )
        elif ctx.path == "/static/board-events.js":
            ctx._send_response(
                _STATIC_BOARD_EVENTS_JS,
                content_type="text/javascript; charset=utf-8",
            )
        elif ctx.path == "/static/add-account-redirect.js":
            ctx._send_response(
                _STATIC_ADD_ACCOUNT_REDIRECT_JS,
                content_type="text/javascript; charset=utf-8",
            )
        elif ctx.path == "/static/settings-loader.js":
            ctx._send_response(
                _STATIC_SETTINGS_LOADER_JS,
                content_type="text/javascript; charset=utf-8",
            )
        elif ctx.path == "/static/appshell-loader.js":
            ctx._send_response(
                _STATIC_APPSHELL_LOADER_JS,
                content_type="text/javascript; charset=utf-8",
            )
        elif ctx.path == "/static/robotsix-ui.js":
            # Vendored at image build time; absent in a bare checkout, where
            # the Settings page shows how to fetch it instead of blank space.
            if _STATIC_ROBOTSIX_UI_JS is None:
                ctx._not_found()
            else:
                ctx._send_response(
                    _STATIC_ROBOTSIX_UI_JS,
                    content_type="text/javascript; charset=utf-8",
                )
        elif ctx.path == "/static/robotsix-ui.css":
            if _STATIC_ROBOTSIX_UI_CSS is None:
                ctx._not_found()
            else:
                ctx._send_response(
                    _STATIC_ROBOTSIX_UI_CSS,
                    content_type="text/css; charset=utf-8",
                )
        else:
            ctx._not_found()

    def serve_archive_proposal(self, ctx: RequestContext) -> None:
        """Serve GET /archive-proposal/{message_id} — return JSON with
        effective subfolder, source, and folder-exists status."""
        from robotsix_auto_mail.db import (
            get_record_by_message_id,
            get_watermark,
        )
        from robotsix_auto_mail.triage import (
            _load_archive_overrides,
            _load_llm_archive_hints,
        )

        path = ctx.path
        prefix = "/archive-proposal/"
        message_id = unquote(path[len(prefix) :])

        archive_root = ctx._effective_archive_root

        with _with_db(ctx.db_path) as conn:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                ctx._not_found()
                return

            subfolder = get_archive_subfolder(
                conn,
                message_id,
                record,
                api_key=resolve_llm_api_key(raise_on_missing=False),
                rules=cast("MailConfig", ctx.mail_config).triage_guidance
                if ctx.mail_config is not None
                else "",
            )
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

        ctx._serve_json(
            {
                "subfolder": subfolder,
                "archive_root": archive_root,
                "folder_exists": folder_exists,
                "overridden": overridden,
                "source": source,
            }
        )

    def serve_board_cards(self, ctx: RequestContext) -> None:
        """Serve GET /board-cards — structured JSON list of board cards.

        Returns a flat JSON array of cards, each with message_id, uid (if
        available), subject, from, date, column, proposed_archive_subfolder,
        and account. No HTML anywhere in the response.

        Query params:
        - ``account`` — resolved by ``_select_account`` before dispatch;
          an unknown account returns 404.
        - ``column`` / ``status`` — optional filter to a single triage
          action (e.g. ``TO_ARCHIVE``, ``INBOX``).
        """
        from urllib.parse import parse_qs, urlsplit

        from robotsix_auto_mail.server.views.board_data import (
            _gather_account_board_data,
        )

        if ctx._aggregate:
            ctx._serve_json(
                {"error": "board-cards is per-account; use ?account=<id>"},
                status=400,
            )
            return

        archive_root = ctx._effective_archive_root
        try:
            data = _gather_account_board_data(ctx.db_path, archive_root=archive_root)
        except Exception:
            ctx._serve_json({"error": "Database unavailable"}, status=503)
            return

        column_buckets = data["column_buckets"]
        archive_subfolders = data.get("archive_subfolders", {})

        # Parse optional column filter.
        qs = parse_qs(urlsplit(ctx.path).query)
        column_filter = qs.get("column")
        if column_filter is None:
            column_filter = qs.get("status")
        column_value = column_filter[0] if column_filter else None

        account_id = ctx._current_account_id or "main"
        cards: list[dict[str, object]] = []

        for column, records in column_buckets.items():
            if column_value is not None and column != column_value:
                continue
            for record in records:
                cards.append(
                    {
                        "message_id": record.message_id,
                        "uid": record.imap_uid,
                        "subject": record.subject,
                        "from": record.sender,
                        "date": record.date,
                        "column": column,
                        "proposed_archive_subfolder": archive_subfolders.get(
                            record.message_id, ""
                        ),
                        "account": account_id,
                    }
                )

        ctx._serve_json({"cards": cards, "account": account_id})

    def serve_archive_folders(self, ctx: RequestContext) -> None:
        """Serve GET /archive-folders — JSON with delimiter + flat subfolder list.

        Lists the real IMAP folder tree under the effective archive root.
        Falls back to the ``archive_structure`` watermark when IMAP is
        unreachable or *mail_config* is not available.

        Short-circuits in aggregate (``?account=__all__``) mode — the JS
        already suppresses the fetch, but a direct ``curl`` must not leak
        data from whichever account's DB ``ctx.db_path`` happens to point at.
        """
        from robotsix_auto_mail.db import get_watermark

        if ctx._aggregate:
            ctx._serve_json({"delimiter": "/", "folders": []})
            return

        archive_root = ctx._effective_archive_root

        # -- try IMAP first -------------------------------------------------
        if ctx.mail_config is not None:
            try:
                from robotsix_auto_mail.imap import ImapClient

                with ImapClient(cast("MailConfig", ctx.mail_config)) as client:
                    all_folders = client.list_folders()
                    delimiter = next(
                        (f.delimiter for f in all_folders if f.delimiter), "/"
                    )
                    _root_prefix = f"{archive_root}{delimiter}"
                    folders: list[str] = []
                    for f in sorted(all_folders, key=lambda f: f.name):
                        if f.name.startswith(_root_prefix) and f.name != archive_root:
                            rel = f.name[len(_root_prefix) :]
                            if delimiter != "/":
                                rel = rel.replace(delimiter, "/")
                            folders.append(rel)
                    ctx._serve_json({"delimiter": "/", "folders": folders})
                    return
            except Exception:
                logger.debug("IMAP folder list fallback to watermark", exc_info=True)
                # Fall through to watermark fallback.

        # -- watermark fallback ---------------------------------------------
        with _with_db(ctx.db_path) as conn:
            archive_raw = get_watermark(conn, "archive_structure")
            existing_folders, delimiter, effective_root = _parse_archive_structure(
                archive_raw, archive_root
            )

        _root_prefix = f"{effective_root}{delimiter}"
        folders = []
        for name in sorted(existing_folders):
            if name.startswith(_root_prefix) and name != effective_root:
                rel = name[len(_root_prefix) :]
                if delimiter != "/":
                    rel = rel.replace(delimiter, "/")
                folders.append(rel)

        ctx._serve_json({"delimiter": "/", "folders": folders})

    def serve_archive_messages(self, ctx: RequestContext, folder: str = "") -> None:
        """Serve GET /archive/<folder>/messages — list messages in an archive folder.

        Connects to IMAP, resolves the folder path under the effective
        archive root, selects it, and returns envelope metadata for every
        message present.  Returns an empty list when the folder is empty.

        Supports an optional ``?limit=N`` query parameter (default 500,
        max 2000) to cap the number of messages returned.

        Short-circuits in aggregate (``?account=__all__``) mode.
        """
        if ctx._aggregate:
            ctx._serve_json({"messages": [], "folder": folder or ""})
            return

        if not ctx._require_imap_configured():
            return

        ok, archive_root = ctx._validate_archive_path(folder)
        if not ok:
            return

        # Resolve the full IMAP folder path.
        # Translate "/" in the URL path to the IMAP delimiter.
        # We'll discover the actual delimiter from the server, but
        # the URL always uses "/".
        full_path = f"{archive_root}/{folder}" if folder else archive_root

        from robotsix_auto_mail.imap import ImapClient, ImapError

        try:
            with ImapClient(cast("MailConfig", ctx.mail_config)) as client:
                # Discover the server's hierarchy delimiter.
                existing = client.list_folders()
                delimiter = next(
                    (f.delimiter for f in existing if f.delimiter),
                    "/",
                )

                # Translate "/" in full_path to the server delimiter.
                translated_path = full_path.replace("/", delimiter)

                # Security: validate the path is under the archive root.
                root_prefix = f"{archive_root.replace('/', delimiter)}{delimiter}"
                if translated_path != archive_root.replace(
                    "/", delimiter
                ) and not translated_path.startswith(root_prefix):
                    ctx._bad_request("Folder path escapes archive root")
                    return

                # Verify the folder exists.
                matching = [f for f in existing if f.name == translated_path]
                if not matching:
                    ctx._not_found()
                    return

                client.select_folder(translated_path)
                all_uids = client.search_uids("ALL")

                # Parse limit query parameter.
                from urllib.parse import parse_qs, urlsplit

                qs = parse_qs(urlsplit(ctx.path).query)
                limit_str = qs.get("limit", ["500"])[0]
                try:
                    limit = min(max(int(limit_str), 1), 2000)
                except ValueError, TypeError:
                    limit = 500

                uids = all_uids[:limit]

                envelopes = client.fetch_envelopes(uids)
        except ImapError as exc:
            ctx._send_response(
                f"IMAP error listing archive folder: {exc}",
                status=502,
            )
            return
        except OSError as exc:
            ctx._send_response(
                f"IMAP connection error: {exc}",
                status=502,
            )
            return

        # Build response — include the effective path for clarity.
        ctx._serve_json(
            {
                "folder": folder if folder else archive_root,
                "full_path": full_path,
                "total": len(all_uids),
                "shown": len(envelopes),
                "messages": envelopes,
            }
        )

    def serve_email_status(self, ctx: RequestContext) -> None:
        """Serve GET /email/{message_id}/status — return triage action as text.

        Returns ``"INBOX"`` when the record exists but has no triage
        decision.  Returns 404 when the message_id is unknown.
        """
        from robotsix_auto_mail.db import get_record_by_message_id

        # Extract the URL-encoded message_id from the path:
        #   "/email/<encoded>/status" → extract and decode.
        path = ctx.path
        prefix = "/email/"
        suffix = "/status"
        encoded_mid = path[len(prefix) : -len(suffix)]
        message_id = unquote(encoded_mid)

        with _with_db(ctx.db_path, skip_migrations=False) as conn:
            record = get_record_by_message_id(conn, message_id)
            if record is None:
                ctx._not_found()
                return
            decision = get_triage_decision(conn, message_id)

        if decision is None:
            ctx._send_response(INBOX)
            return

        ctx._send_response(decision.action)

    def serve_email_detail(self, ctx: RequestContext) -> None:
        """Serve GET /email/{message_id} — full detail page.

        Supports ``?embed=1`` to return a fragment suitable for an
        iframe (no full-page chrome, no refresh).

        In embed mode the account cookie is cleared so the parent
        board's cookie is preserved (the same fix as commit ``34f2479``
        for board-card actions).
        """
        from urllib.parse import parse_qs, urlparse

        path = ctx.path
        prefix = "/email/"

        # Separate path from query string.
        parsed = urlparse(path)
        message_id = unquote(parsed.path[len(prefix) :])
        qs = parse_qs(parsed.query)
        embed = qs.get("embed", ["0"])[0] == "1"

        # Preserve the parent board's account cookie: when the detail
        # pane iframe loads with ``?account=<cardAccount>`` the request
        # arms a Set-Cookie that would overwrite whatever cookie the
        # parent board set (e.g. ``__all__`` for the aggregate view).
        # Clearing ``_account_cookie`` prevents that emission.
        if embed:
            ctx._account_cookie = None

        try:
            detail_html = _build_detail_html(
                ctx.db_path,
                message_id,
                embed=embed,
                current_account_id=ctx._current_account_id,
            )
        except Exception:
            ctx._send_response("Database unavailable", status=503)
            return

        if detail_html is None:
            ctx._not_found()
            return

        ctx._send_response(detail_html, content_type="text/html; charset=utf-8")
