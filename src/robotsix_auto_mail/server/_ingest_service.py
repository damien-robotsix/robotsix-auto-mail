"""Force-fetch (immediate ingest) service for the board server."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, cast

from robotsix_auto_mail.core._constants import _INGEST_RUN_STATE_KEY
from robotsix_auto_mail.server._services import Service
from robotsix_auto_mail.server.adapters import (
    _run_ingest_background,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.config import MailAccountsConfig
    from robotsix_auto_mail.server._board_handler_protocol import RequestContext


class IngestService(Service):
    """Stateless service providing the POST /force-fetch handler."""

    def handle_force_fetch(self, ctx: RequestContext) -> None:
        """Process POST /force-fetch — trigger an immediate mailbox fetch.

        Idempotent: if an ingest is already running the request is a no-op
        that redirects to ``/board`` immediately.  Otherwise the
        ``ingest_run:state`` watermark is set and a daemon thread is
        spawned to run an ingest cycle; :func:`_ingest_cycle` clears the
        watermark in a ``finally`` block so the board always recovers.
        """
        if not ctx._launch_background_worker(_INGEST_RUN_STATE_KEY):
            return

        if ctx._aggregate and ctx.accounts is not None:
            accounts = cast("MailAccountsConfig", ctx.accounts)
            for acct in accounts.accounts:
                threading.Thread(
                    target=_run_ingest_background,
                    args=(acct.config.db_path, acct.config),
                    daemon=True,
                ).start()
        else:
            threading.Thread(
                target=_run_ingest_background,
                args=(ctx.db_path, ctx.mail_config),
                daemon=True,
            ).start()

        ctx._redirect("/board", code=302)
