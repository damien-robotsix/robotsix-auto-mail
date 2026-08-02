"""Serve subcommand handler and background reconcile loop."""

from __future__ import annotations

import argparse
import errno
import sys
import threading
import time

from robotsix_auto_mail.cli.commands_triage import _clear_stale_triage_state
from robotsix_auto_mail.config import MailAccountsConfig
from robotsix_auto_mail.core._constants import (
    _RECONCILE_STATE_KEY,
    _WATERMARK_RUNNING,
)


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

    Reloads the accounts container from the config file on every cycle
    so newly added accounts (via web UI) are picked up without a restart.
    When the config file is unreadable the loop keeps using the last
    successfully loaded snapshot.
    """
    import threading

    from robotsix_auto_mail.config import load_accounts
    from robotsix_auto_mail.db import get_watermark, init_db, set_watermark
    from robotsix_auto_mail.server.adapters import _run_reconcile_background

    _default_fallback_minutes = 15

    while True:
        # Reload accounts from the config file on every cycle so accounts
        # added via the web UI are picked up without a restart.
        try:
            accounts = load_accounts()
            from robotsix_auto_mail.settings import merge_settings_store_accounts

            accounts = merge_settings_store_accounts(accounts)
        except Exception:  # noqa: S110
            # Keep using the last-known snapshot when the config file
            # is temporarily unreadable.
            pass

        if accounts.accounts:
            interval_minutes = max(
                1,
                min(acct.config.ingest_interval_minutes for acct in accounts.accounts),
            )
        else:
            interval_minutes = _default_fallback_minutes
        for acct in accounts.accounts:
            if not acct.config.password.get_secret_value():
                continue
            try:
                conn = init_db(acct.config.db_path, skip_migrations=True)
                try:
                    if get_watermark(conn, _RECONCILE_STATE_KEY) != _WATERMARK_RUNNING:
                        set_watermark(conn, _RECONCILE_STATE_KEY, _WATERMARK_RUNNING)
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


def _ingest_loop(accounts: MailAccountsConfig) -> None:
    """Periodically ingest new mail for every account in a background daemon thread.

    Runs :func:`_ingest_cycle` for each account with a configured password
    on an interval derived from the minimum ``ingest_interval_minutes``
    across all accounts.  Reloads the accounts container from the config
    file on every cycle so newly added accounts (via web UI) are picked up
    without a restart.

    This loop is the background-ingest counterpart to ``_reconcile_loop``
    (which only runs reconciliation/tombstone cleanup).  Starting this in
    the serve command makes the web server self-sufficient — mails are
    fetched automatically without requiring a separate ``ingest --watch``
    process.
    """
    import logging

    from robotsix_auto_mail.cli.commands_ingest import _ingest_cycle
    from robotsix_auto_mail.config import load_accounts

    logger = logging.getLogger(__name__)

    _default_fallback_minutes = 15

    while True:
        # Reload accounts from the config file on every cycle so accounts
        # added via the web UI are picked up without a restart.
        try:
            accounts = load_accounts()
            from robotsix_auto_mail.settings import merge_settings_store_accounts

            accounts = merge_settings_store_accounts(accounts)
        except Exception:  # noqa: S110
            # Keep using the last-known snapshot when the config file
            # is temporarily unreadable.
            pass

        if accounts.accounts:
            interval_minutes = max(
                1,
                min(acct.config.ingest_interval_minutes for acct in accounts.accounts),
            )
        else:
            interval_minutes = _default_fallback_minutes

        for acct in accounts.accounts:
            if not acct.config.password.get_secret_value():
                logger.debug(
                    "Skipping ingest for account %r: no password configured",
                    acct.account_id,
                )
                continue
            try:
                _ingest_cycle(acct.config, dry_run=False)
            except Exception:
                logger.exception("Ingest cycle failed for account %r", acct.account_id)

        time.sleep(interval_minutes * 60)


def _import_settings_from_central_deploy(accounts: MailAccountsConfig) -> None:
    """Seed each account's settings store from central-deploy on first boot.

    The import is idempotent — it only runs when the store is empty and
    the ``CENTRAL_DEPLOY_EXPORT_URL`` environment variable is set.
    Failures are logged but never prevent the server from starting.
    """
    import logging
    import os

    logger = logging.getLogger(__name__)

    if not os.environ.get("CENTRAL_DEPLOY_EXPORT_URL"):
        return

    from robotsix_auto_mail.db import init_db
    from robotsix_auto_mail.settings import SettingsStore, import_from_central_deploy

    for acct in accounts.accounts:
        try:
            conn = init_db(acct.config.db_path, skip_migrations=True)
            try:
                store = SettingsStore(acct.config.db_path)
                import_from_central_deploy(store, conn)
            finally:
                conn.close()
        except Exception:
            logger.exception(
                "Failed to import settings for account %s", acct.account_id
            )


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

    When no accounts are configured (fresh deploy) the server starts with
    an in-memory database so the add-account form and health endpoints
    are available.

    Accounts discovered from existing settings stores (e.g. accounts added
    via the web UI whose config-file entries were overwritten by a deploy)
    are merged into the container so they survive restarts.
    """
    import logging
    from http.server import ThreadingHTTPServer

    from robotsix_auto_mail.server import make_board_handler

    _logger = logging.getLogger(__name__)

    # Merge accounts discovered from settings stores that are not already
    # in the config file.  This ensures accounts added via the web UI
    # survive even when the deploy system overwrites config/config.json.
    from robotsix_auto_mail.settings import merge_settings_store_accounts

    accounts = merge_settings_store_accounts(accounts)
    if not default_account_id and accounts.default_account_id:
        default_account_id = accounts.default_account_id

    if accounts.accounts:
        default = accounts.get(default_account_id)
        db_path = default.config.db_path
        mail_config = default.config
    else:
        db_path = ":memory:"
        mail_config = None

    # One-time import: seed the per-component settings store from
    # central-deploy's export endpoint on first boot.  Each account's
    # store is seeded independently; the import is idempotent (skips
    # when the store is already populated).
    _import_settings_from_central_deploy(accounts)

    handler_class = make_board_handler(
        db_path,
        mail_config=mail_config,
        accounts=accounts,
        default_account_id=default_account_id if accounts.accounts else None,
    )

    # Self-heal any orphaned ``triage_run:state == "running"`` watermark left
    # behind by a SIGKILL'd worker thread on a prior container run.  At a fresh
    # process start there is no live worker, so any such flag is safe to clear.
    _clear_stale_triage_state(accounts)

    threading.Thread(target=_reconcile_loop, args=(accounts,), daemon=True).start()
    threading.Thread(target=_ingest_loop, args=(accounts,), daemon=True).start()

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
