"""Reconciliation-launcher service for the board server."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, cast

from robotsix_auto_mail.core._constants import _RECONCILE_STATE_KEY
from robotsix_auto_mail.server._services import Service
from robotsix_auto_mail.server.adapters import (
    _run_reconcile_background,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailAccountsConfig
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext


class ReconcileService(Service):
    """Stateless service providing the POST /reconcile handler."""

    def handle_reconcile(self, ctx: RequestContext) -> None:
        """Process POST /reconcile — launch reconciliation in a background thread.

        Idempotent: if reconciliation is already running the request is a
        no-op that redirects to ``/board`` immediately.  Otherwise the
        ``reconcile:state`` watermark is set and a daemon thread is spawned;
        the thread clears the watermark in a ``finally`` block so the board
        always recovers.
        """
        if not ctx._launch_background_worker(_RECONCILE_STATE_KEY):
            return

        if ctx._aggregate and ctx.accounts is not None:
            accounts = cast("MailAccountsConfig", ctx.accounts)
            for acct in accounts.accounts:
                threading.Thread(
                    target=_run_reconcile_background,
                    args=(acct.config.db_path, acct.config),
                    daemon=True,
                ).start()
        else:
            threading.Thread(
                target=_run_reconcile_background,
                args=(ctx.db_path, ctx.mail_config),
                daemon=True,
            ).start()

        ctx._redirect("/board", code=302)
