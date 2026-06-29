"""Reconciliation-launcher mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from robotsix_auto_mail._constants import _RECONCILE_STATE_KEY
from robotsix_auto_mail.server.adapters import (
    _run_reconcile_background,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailAccountsConfig  # noqa: F401


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
        if not self._launch_background_worker(_RECONCILE_STATE_KEY):
            return

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
