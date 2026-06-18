"""Triage-launcher mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from robotsix_auto_mail.server.adapters import (
    _run_triage_background,
)


class _TriageMixin:
    """Mixin providing triage-related POST handlers for BoardHandler."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_run_triage(self) -> None:
        """Process POST /run-triage — launch triage agent in a background thread.

        Idempotent: if triage is already running the request is a no-op
        that redirects to ``/board`` immediately.  Otherwise a watermark
        is set and a daemon thread is spawned to run the agent; the
        thread clears the watermark in a ``finally`` block so the board
        always recovers.
        """
        self._launch_background_worker(
            "triage_run:state",
            _run_triage_background,
            (
                self.db_path,
                self.mail_config.username if self.mail_config is not None else None,
            ),
        )

    def _handle_force_triage_column(self) -> None:
        """Process POST /force-triage-column — reset triage decisions for
        one column, then launch the triage agent in a background thread.

        Follows the same pattern as :meth:`_handle_run_triage`: decisions
        are deleted, then the global agent is spawned (or joined if
        already running).  The watermark guard ensures only one triage
        run is in flight at a time.
        """
        import urllib.parse

        from robotsix_auto_mail.db import (
            VALID_TRIAGE_ACTIONS,
            init_db,
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
        self._launch_background_worker(
            "triage_run:state",
            _run_triage_background,
            (
                self.db_path,
                self.mail_config.username if self.mail_config is not None else None,
            ),
        )
