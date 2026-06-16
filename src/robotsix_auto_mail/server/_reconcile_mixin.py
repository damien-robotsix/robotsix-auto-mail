"""Reconciliation-launcher mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

from typing import TYPE_CHECKING

from robotsix_auto_mail.server.adapters import (
    _run_reconcile_background,
)


class _ReconcileMixin:
    """Mixin providing the POST /reconcile handler for BoardHandler."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_reconcile(self) -> None:
        """Process POST /reconcile — launch reconciliation in a background thread.

        Idempotent: if reconciliation is already running the request is a
        no-op that redirects to ``/board`` immediately.  Otherwise the
        ``reconcile:state`` watermark is set and a daemon thread is spawned;
        the thread clears the watermark in a ``finally`` block so the board
        always recovers.
        """
        import threading

        from robotsix_auto_mail.db import get_watermark, init_db, set_watermark

        conn = init_db(self.db_path, skip_migrations=True)
        try:
            if get_watermark(conn, "reconcile:state") == "running":
                self._redirect("/board", code=302)
                return
            set_watermark(conn, "reconcile:state", "running")
        finally:
            conn.close()

        if self._aggregate and self.accounts is not None:
            accounts = self.accounts  # type: MailAccountsConfig
            for acct in accounts.accounts:
                threading.Thread(
                    target=_run_reconcile_background,
                    args=(acct.config.db_path, acct.config),
                    daemon=True,
                ).start()
        else:
            threading.Thread(
                target=_run_reconcile_background,
                args=(self.db_path, self.mail_config),
                daemon=True,
            ).start()

        self._redirect("/board", code=302)
