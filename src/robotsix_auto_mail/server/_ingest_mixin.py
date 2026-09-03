"""Force-fetch (immediate ingest) mixin for the board server."""

# mypy: disable-error-code="attr-defined"

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from robotsix_auto_mail.core._constants import _INGEST_RUN_STATE_KEY
from robotsix_auto_mail.server.adapters import (
    _run_ingest_background,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailAccountsConfig  # noqa: F401


class _IngestMixin:
    """Mixin providing the POST /force-fetch handler for BoardHandler."""

    if TYPE_CHECKING:
        from ._board_handler_protocol import BoardHandlerProtocol

    self: BoardHandlerProtocol

    def _handle_force_fetch(self) -> None:
        """Process POST /force-fetch — trigger an immediate mailbox fetch.

        Idempotent: if an ingest is already running the request is a no-op
        that redirects to ``/board`` immediately.  Otherwise the
        ``ingest_run:state`` watermark is set and a daemon thread is
        spawned to run an ingest cycle; :func:`_ingest_cycle` clears the
        watermark in a ``finally`` block so the board always recovers.
        """
        if not self._launch_background_worker(_INGEST_RUN_STATE_KEY):
            return

        if self._aggregate and self.accounts is not None:
            accounts = self.accounts  # type: MailAccountsConfig
            for acct in accounts.accounts:
                threading.Thread(
                    target=_run_ingest_background,
                    args=(acct.config.db_path, acct.config),
                    daemon=True,
                ).start()
        else:
            threading.Thread(
                target=_run_ingest_background,
                args=(self.db_path, self.mail_config),
                daemon=True,
            ).start()

        self._redirect("/board", code=302)
