"""Serve subcommand handler and background reconcile loop."""

from __future__ import annotations

import argparse
import errno
import sys
import threading
import time

from robotsix_auto_mail._constants import _RECONCILE_STATE_KEY
from robotsix_auto_mail.cli.commands_triage import _clear_stale_triage_state
from robotsix_auto_mail.config import MailAccountsConfig


def register_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    from robotsix_auto_mail.cli import _add_account_arg

    parser = subparsers.add_parser("serve", help="Start the web board server")
    _add_account_arg(parser)
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on (default: %(default)s)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help=(
            "Address to bind the board server to (default: %(default)s). "
            "Use 0.0.0.0 to listen on all interfaces (e.g. inside Docker "
            "with host-level network isolation)."
        ),
    )


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
                    if get_watermark(conn, _RECONCILE_STATE_KEY) != "running":
                        set_watermark(conn, _RECONCILE_STATE_KEY, "running")
                        threading.Thread(
                            target=_run_reconcile_background,
                            args=(acct.config.db_path, acct.config),
                            daemon=True,
                        ).start()
                finally:
                    conn.close()
            except Exception:  # noqa: S110  # nosec B110  # lgtm[py/empty-except]
                # A bad DB must not kill the loop.
                pass
        time.sleep(interval_minutes * 60)


def _cmd_serve(
    accounts: MailAccountsConfig,
    *,
    default_account_id: str,
    port: int,
    host: str = "127.0.0.1",
) -> int:
    """Run the serve subcommand: start the web board HTTP server.

    The full *accounts* container drives per-request account resolution;
    *default_account_id* names the account whose config is used for
    server startup (initial ``db_path``); it
    is also the per-request fallback for single-account setups.  For
    multi-account setups the board always defaults to the aggregate
    (``__all__``) view — ``default_account_id`` is not consulted for the
    initial board view.  Returns 0 on clean shutdown, 1 if the port is
    already in use.
    """
    from http.server import ThreadingHTTPServer

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

    print(f"Serving board on http://{host}:{port}/board")
    try:
        # Binding to 127.0.0.1 by default: the board is a local dev tool.
        # Pass --host 0.0.0.0 to expose it on all interfaces (e.g. Docker
        # where network isolation is enforced at the container level).
        # lgtm[py/clear-text-transmission-sensitive-data]
        server = ThreadingHTTPServer((host, port), handler_class)
        server.serve_forever()
    except KeyboardInterrupt:
        print("Shutting down.")
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"Port {port} is already in use.", file=sys.stderr)
            return 1
        raise
    finally:
        pass
    return 0
