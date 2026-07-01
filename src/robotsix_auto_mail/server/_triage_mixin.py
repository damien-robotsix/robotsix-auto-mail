"""Triage-launcher mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from robotsix_auto_mail._constants import _TRIAGE_RUN_STATE_KEY
from robotsix_auto_mail.server._constants import _with_db
from robotsix_auto_mail.server.adapters import (
    _run_triage_background,
)
from robotsix_auto_mail.triage import resolve_rules_path


def _rules_path_str(mail_config: object | None, db_path: str) -> str | None:
    """Resolve the per-account triage-rules path as a string (or ``None``)."""
    rules_path = getattr(mail_config, "triage_rules_path", "") if mail_config else ""
    resolved = resolve_rules_path(db_path=db_path, rules_path=rules_path)
    return str(resolved) if resolved else None


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
            _TRIAGE_RUN_STATE_KEY,
            _run_triage_background,
            (
                self.db_path,
                self.mail_config.username if self.mail_config is not None else None,
                _rules_path_str(self.mail_config, self.db_path),
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
        from robotsix_auto_mail.db import (
            VALID_TRIAGE_ACTIONS,
        )
        from robotsix_auto_mail.triage import (
            TriageError,
            delete_triage_decisions_by_action,
        )

        # -- parse body ---------------------------------------------------
        params = self._parse_request_body("action")
        action = params["action"]
        if action not in VALID_TRIAGE_ACTIONS:
            self._bad_request(f"Invalid triage action: {action!r}")
            return

        # -- clear decisions ----------------------------------------------
        try:
            with _with_db(self.db_path) as conn:
                delete_triage_decisions_by_action(conn, action)
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
            _TRIAGE_RUN_STATE_KEY,
            _run_triage_background,
            (
                self.db_path,
                self.mail_config.username if self.mail_config is not None else None,
                _rules_path_str(self.mail_config, self.db_path),
            ),
        )
