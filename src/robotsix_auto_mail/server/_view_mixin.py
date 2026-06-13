"""View-serving mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import unquote

from robotsix_auto_mail.config import DEFAULT_ARCHIVE_ROOT
from robotsix_auto_mail.server._constants import (
    _STATIC_AUTOMAIL_BOARD_CSS,
    _STATIC_BOARD_AUTOMAIL_JS,
    _STATIC_BOARD_CSS,
    _STATIC_BOARD_JS,
    _parse_archive_structure,
)
from robotsix_auto_mail.server.views import (
    _build_board_content,
    _build_board_html,
    _build_detail_html,
    _build_global_board_content,
    _build_global_board_html,
    _build_rules_html,
)
from robotsix_auto_mail.triage import (
    get_archive_subfolder,
    get_triage_decision,
)


class _BoardViewMixin:
    """Mixin providing view-serving methods for the board handler."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

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
                user_email=self.mail_config.username if self.mail_config else None,
            )
        except Exception:
            self._send_response("Database unavailable", status=503)
            return

        self._send_response(body, content_type="text/html; charset=utf-8")

    def _serve_rules(self) -> None:
        """Render and serve the rules management page."""
        if self._aggregate and self.accounts is not None:
            try:
                body = _build_rules_html(
                    "__aggregate__",
                    accounts=self.accounts,
                    current_account_id=self._current_account_id,
                )
            except Exception:
                self._send_response("Database unavailable", status=503)
                return
            self._send_response(body, content_type="text/html; charset=utf-8")
            return

        try:
            body = _build_rules_html(
                self.db_path,
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
            payload = _build_board_content(
                self.db_path,
                archive_root=archive_root,
                user_email=self.mail_config.username if self.mail_config else None,
            )
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

        from robotsix_auto_mail.imap import ImapClient, ImapError, is_system_folder

        try:
            with ImapClient(self.mail_config) as client:
                folders = [
                    info.name
                    for info in client.list_folders()
                    if not is_system_folder(info)
                ]
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

            subfolder = get_archive_subfolder(
                conn, message_id, record,
                api_key=self.mail_config.llm_api_key if self.mail_config else "",
                user_email=self.mail_config.username if self.mail_config else None,
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
