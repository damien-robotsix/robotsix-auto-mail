"""Triage-launcher service for the board server."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from robotsix_auto_mail.core._constants import _TRIAGE_RUN_STATE_KEY
from robotsix_auto_mail.server._constants import _with_db
from robotsix_auto_mail.server._request_helpers import parse_request_body
from robotsix_auto_mail.server._services import Service
from robotsix_auto_mail.server.adapters import (
    _run_triage_background,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailConfig
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext

logger = logging.getLogger(__name__)


class TriageService(Service):
    """Stateless service providing triage-related POST handlers."""

    def _launch_triage(self, ctx: RequestContext) -> None:
        """Launch the triage agent in a background thread (shared helper)."""
        cfg = cast("MailConfig | None", ctx.mail_config)
        guidance = cfg.triage_guidance if cfg is not None else ""
        ctx._launch_background_worker(
            _TRIAGE_RUN_STATE_KEY,
            _run_triage_background,
            (
                ctx.db_path,
                cfg.username if cfg is not None else None,
                guidance,
            ),
        )

    def handle_run_triage(self, ctx: RequestContext) -> None:
        """Process POST /run-triage — launch triage agent in a background thread.

        Idempotent: if triage is already running the request is a no-op
        that redirects to ``/board`` immediately.  Otherwise a watermark
        is set and a daemon thread is spawned to run the agent; the
        thread clears the watermark in a ``finally`` block so the board
        always recovers.
        """
        self._launch_triage(ctx)

    def handle_force_triage_column(self, ctx: RequestContext) -> None:
        """Process POST /force-triage-column — reset triage decisions for
        one column, then launch the triage agent in a background thread.

        Follows the same pattern as :meth:`handle_run_triage`: decisions
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
        params = parse_request_body(ctx, "action")
        action = params["action"]
        if action not in VALID_TRIAGE_ACTIONS:
            ctx._bad_request(f"Invalid triage action: {action!r}")
            return

        # -- clear decisions ----------------------------------------------
        try:
            with _with_db(ctx.db_path) as conn:
                delete_triage_decisions_by_action(conn, action)
        except TriageError:
            logger.exception("Triage handler failed")
            ctx._bad_request("Invalid request")
            return
        except Exception as exc:
            ctx._problem(
                status=503,
                kind="triage-failed",
                title="Triage Failed",
                detail=str(exc),
            )
            return

        # -- launch triage (same pattern as handle_run_triage) ------------
        self._launch_triage(ctx)
