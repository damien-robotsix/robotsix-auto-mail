"""Serve subcommand handler and background reconcile loop."""

from __future__ import annotations

import errno
import sys
import threading
import time

from robotsix_auto_mail.cli.commands_triage import _clear_stale_triage_state
from robotsix_auto_mail.config import MailAccountsConfig


def _reconcile_loop(accounts: MailAccountsConfig) -> None:
    """Periodically reconcile every account in a background daemon thread.

    For each account, spawns ``_run_reconcile_background`` in its own
    daemon thread (non-blocking) so one slow IMAP server doesn't delay
    reconciliation of other accounts.  Uses the per-account
    ``reconcile:state`` watermark to prevent overlapping runs.
    """
    import threading

    from robotsix_auto_mail.db import get_watermark, init_db, set_watermark
    from robotsix_auto_mail.server.adapters import _run_reconcile_background

    interval_minutes = max(
        1, min(acct.config.ingest_interval_minutes for acct in accounts.accounts)
    )
    while True:
        for acct in accounts.accounts:
            try:
                conn = init_db(acct.config.db_path, skip_migrations=True)
                try:
                    if get_watermark(conn, "reconcile:state") != "running":
                        set_watermark(conn, "reconcile:state", "running")
                        threading.Thread(
                            target=_run_reconcile_background,
                            args=(acct.config.db_path, acct.config),
                            daemon=True,
                        ).start()
                finally:
                    conn.close()
            except Exception:  # noqa: S110  # nosec B110
                # A bad DB must not kill the loop.
                pass
        time.sleep(interval_minutes * 60)


def _cmd_serve(
    accounts: MailAccountsConfig, *, default_account_id: str, port: int
) -> int:
    """Run the serve subcommand: start the web board HTTP server.

    The full *accounts* container drives per-request account resolution;
    *default_account_id* names the account served when a request omits
    ``?account=``.  Returns 0 on clean shutdown, 1 if the port is already
    in use.
    """
    from http.server import HTTPServer

    from robotsix_auto_mail.board_agent import start_board_agent, stop_board_agent
    from robotsix_auto_mail.server import make_board_handler

    default = accounts.get(default_account_id)
    handler_class = make_board_handler(
        default.config.db_path,
        mail_config=default.config,
        accounts=accounts,
        default_account_id=default_account_id,
    )

    # Self-heal any orphaned ``triage_run:state == "running"`` watermark left
    # behind by a SIGKILL'd worker thread on a prior container run.  At a fresh
    # process start there is no live worker, so any such flag is safe to clear.
    _clear_stale_triage_state(accounts)

    threading.Thread(target=_reconcile_loop, args=(accounts,), daemon=True).start()

    board_agent_handle = None
    if default.config.board_agent_enabled:
        board_agent_handle = start_board_agent(default.config)

    print(f"Serving board on http://0.0.0.0:{port}/board")
    try:
        # Binding to 0.0.0.0 is intentional: ``serve_board`` is a local dev
        # convenience tool, not a production server.
        server = HTTPServer(("0.0.0.0", port), handler_class)  # noqa: S104  # nosec B104
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down.")
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"Port {port} is already in use.", file=sys.stderr)
            return 1
        raise
    finally:
        stop_board_agent(board_agent_handle)
    return 0
