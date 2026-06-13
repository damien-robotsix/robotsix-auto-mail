"""Triage-launcher mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

from robotsix_auto_mail.server.adapters import (
    _run_folder_triage_background,
    _run_triage_background,
)
from robotsix_auto_mail.triage import (
    TriageError,
    delete_active_rule,
    set_rule_state,
)


class _TriageMixin:
    """Mixin providing triage-related POST handlers for BoardHandler."""

    # -- mypy: declare attributes and methods provided by BoardHandler ---
    if TYPE_CHECKING:
        from collections.abc import Mapping as _Mapping

        from robotsix_auto_mail.config import (
            MailAccountsConfig as _MailAccountsConfig,
        )
        from robotsix_auto_mail.config import (
            MailConfig as _MailConfig,
        )

        db_path: str
        mail_config: _MailConfig | None
        accounts: _MailAccountsConfig | None
        _current_account_id: str | None
        _aggregate: bool
        _account_cookie: str | None
        default_account_id: str | None

        def _send_response(
            self,
            body: bytes | str,
            status: int = 200,
            content_type: str = "text/plain; charset=utf-8",
        ) -> None: ...
        def _redirect(self, location: str, code: int = 301) -> None: ...
        def _not_found(self) -> None: ...
        def _bad_request(self, message: str) -> None: ...
        def _serve_json(
            self, payload: _Mapping[str, object], status: int = 200
        ) -> None: ...

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

    def _handle_rule_delete(self) -> None:
        """Process POST /rule-delete — delete an active triage rule."""
        from robotsix_auto_mail.db import init_db

        content_length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(content_length).decode("utf-8")
        fields = parse_qs(raw)

        fingerprint = (fields.get("fingerprint") or [""])[0].strip()

        if not fingerprint:
            self._bad_request("Missing fingerprint")
            return

        conn = init_db(self.db_path)
        try:
            delete_active_rule(conn, fingerprint)
        except TriageError:
            self._not_found()
            return
        finally:
            conn.close()

        self._redirect("/rules", code=302)

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
