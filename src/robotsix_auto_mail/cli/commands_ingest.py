"""Ingest command handlers — extracted from commands.py."""

from __future__ import annotations

import argparse
import contextlib
import pathlib
import signal
import sys

import robotsix_auto_mail.cli as _cli  # lgtm[py/unsafe-cyclic-import]
from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.core._constants import (
    _INGEST_RUN_STATE_KEY,
    _WATERMARK_IDLE,
    _WATERMARK_RUNNING,
)
from robotsix_auto_mail.core.health import probe_account, utcnow
from robotsix_auto_mail.db import set_watermark
from robotsix_auto_mail.db.queries import write_account_health
from robotsix_auto_mail.pipeline import IngestResult, reconcile_records


def _idle_watch_loop(heartbeat_file: str | None) -> int:
    """Idle heartbeat loop for watch mode when no accounts are ingestable.

    Touches *heartbeat_file* (if set) every cycle so a Docker HEALTHCHECK
    can verify the process is alive, and re-loads the config on each cycle
    so newly added accounts are picked up without a restart.  When an
    account with a password appears the function delegates to
    :func:`_cmd_ingest` to begin normal ingestion.

    Returns 0 on clean shutdown (Ctrl-C / SIGTERM).
    """
    from robotsix_auto_mail.config import ConfigurationError

    def _handle_sigterm(_sig: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        while True:
            sys.stdout.write("idle: no watchable accounts configured; waiting\n")
            sys.stdout.flush()

            if heartbeat_file is not None:
                try:
                    pathlib.Path(heartbeat_file).touch()
                except Exception as exc:
                    sys.stderr.write(f"Heartbeat write failed: {exc}\n")

            # Re-check the config so freshly added accounts are picked up.
            try:
                fresh = _cli.load_accounts()
                from robotsix_auto_mail.settings import merge_settings_store_accounts

                fresh = merge_settings_store_accounts(fresh)
            except ConfigurationError:
                fresh = None

            if fresh is not None:
                active = [
                    a for a in fresh.accounts if a.config.password.get_secret_value()
                ]
                if active:
                    # Transition to normal watch mode.
                    return _cmd_ingest(
                        fresh,
                        watch=True,
                        heartbeat_file=heartbeat_file,
                    )

            _cli.time.sleep(60)
    except KeyboardInterrupt, SystemExit:
        sys.stdout.write("\nWatch stopped.\n")
        return 0


def register_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``ingest`` subcommand and its arguments.

    The subcommand fetches new mail from IMAP, parses it, stores it in the
    local database, and updates the watermark.  Supports single or
    all-accounts operation, dry-run mode, and a watch loop with liveness
    heartbeat file.

    Args:
        subparsers: The argparse subparsers group to attach the parser to.
    """
    parser = subparsers.add_parser("ingest", help="Fetch new mail and store it locally")
    ingest_account_group = parser.add_mutually_exclusive_group()
    ingest_account_group.add_argument(
        "--account",
        metavar="ID",
        default=None,
        help="Account id to ingest.",
    )
    ingest_account_group.add_argument(
        "--all-accounts",
        action="store_true",
        default=False,
        help="Ingest every configured account (the default when --account is omitted).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Fetch and parse messages without storing or advancing watermark",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        default=False,
        help=(
            "Keep running, ingesting on an interval (minutes) set by "
            "ingest.interval_minutes in the config (default 15)"
        ),
    )
    parser.add_argument(
        "--heartbeat-file",
        default=None,
        metavar="PATH",
        help=(
            "In --watch mode, touch this file at the end of each poll cycle "
            "so a Docker HEALTHCHECK can verify the loop is alive. "
            "No file is written when omitted."
        ),
    )


def _ingest_cycle(config: MailConfig, *, dry_run: bool = False) -> int:
    """Run a single ingest pass: fetch, parse, store, and update watermark.

    Returns 0 when the pipeline runs (including per-message errors),
    or 1 for a fatal connection failure (ImapClient raise).
    """
    result: IngestResult | None = None
    conn = _cli.init_db(config.db_path)
    set_watermark(conn, _INGEST_RUN_STATE_KEY, _WATERMARK_RUNNING)
    success = False
    try:
        with _cli.ImapClient(config) as imap_client:
            result = _cli.ingest_mail(conn, imap_client, config, dry_run=dry_run)
            if not dry_run and result is not None:
                try:
                    healed, removed = reconcile_records(
                        conn, imap_client, monitored_folder=config.imap_folder
                    )
                    if healed or removed:
                        sys.stdout.write(
                            f"Reconciliation: {healed} healed, {removed} removed\n"
                        )
                except Exception:
                    sys.stderr.write("Reconciliation failed (will retry next cycle)\n")
    except Exception as exc:
        # Fatal connection failure — ImapClient(config) raised.
        sys.stderr.write(f"Connection FAILED: {exc}\n")
        with contextlib.suppress(Exception):
            write_account_health(
                conn, status="failed", error=str(exc), checked_at=utcnow()
            )
        result = None
    else:
        # Successful ingest cycle (result may still be None on dry run
        # but the connection itself succeeded).
        success = not dry_run and result is not None
    finally:
        # Record ingest-complete watermarks so the board UI can show
        # ingest liveness (running/idle, last-run timestamps).
        with contextlib.suppress(Exception):
            set_watermark(conn, _INGEST_RUN_STATE_KEY, _WATERMARK_IDLE)
            now_iso = utcnow().isoformat()
            set_watermark(conn, "ingest_run:last_at", now_iso)
            if success:
                set_watermark(conn, "ingest_run:last_success_at", now_iso)
        if success:
            with contextlib.suppress(Exception):
                write_account_health(conn, status="ok", error=None, checked_at=utcnow())
        conn.close()

    # If ImapClient(config) raised before ingest_mail ran, result is None.
    if result is None:
        return 1

    # -- Print summary -------------------------------------------------------
    if dry_run:
        sys.stdout.write("DRY RUN — nothing stored\n")

    sys.stdout.write(f"Fetched: {result.total_fetched:>2} messages\n")
    sys.stdout.write(f"Stored:  {result.stored:>2} new\n")
    sys.stdout.write(f"Skipped: {result.skipped:>2} duplicate\n")
    sys.stdout.write(f"Triaged: {result.triaged:>2}\n")
    sys.stdout.write(f"Errors:  {len(result.errors):>2}\n")

    if result.errors:
        for err_obj in result.errors:
            # Guard against empty message_id.
            mid = f" ({err_obj.message_id})" if err_obj.message_id else ""
            sys.stdout.write(f"  UID {err_obj.uid}{mid}: {err_obj.error}\n")

    return 0


def _cmd_ingest(
    accounts: MailAccountsConfig | None,
    *,
    account_id: str | None = None,
    all_accounts: bool = False,
    dry_run: bool = False,
    watch: bool = False,
    heartbeat_file: str | None = None,
) -> int:
    """Run the ingest subcommand for one or more accounts.

    When *account_id* is given, only that account is processed (exiting 1
    with the valid ids on an unknown id).  Otherwise every configured account
    is processed in order.  A per-account header is printed only when more
    than one account is processed.

    In watch mode it loops forever, running an ingest cycle for each selected
    account every interval.  A failed cycle is logged and the loop continues;
    Ctrl-C or SIGTERM exits cleanly with 0.

    When *accounts* is ``None`` (zero-account config) and *watch* is true the
    function enters an idle heartbeat loop that re-checks the config on each
    cycle so newly added accounts are picked up without a restart.
    """
    if accounts is None:
        if not watch:
            sys.stderr.write("No accounts configured; nothing to do.\n")
            return 0
        return _idle_watch_loop(heartbeat_file)

    # Merge accounts discovered from settings stores so accounts added via
    # the web UI survive a deploy-system overwrite of config/config.json.
    from robotsix_auto_mail.settings import merge_settings_store_accounts

    accounts = merge_settings_store_accounts(accounts)

    if account_id is not None:
        try:
            selected = [accounts.get(account_id)]
        except ConfigurationError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            sys.exit(1)
    else:
        selected = list(accounts.accounts)

    # -- skip accounts that have no password configured -----------------
    skipped: list[str] = []
    active: list[MailAccount] = []
    for account in selected:
        if not account.config.password.get_secret_value():
            skipped.append(account.account_id)
        else:
            active.append(account)
    if skipped:
        for aid in skipped:
            sys.stderr.write(
                f"Account '{aid}' has no password configured; "
                "skipping until credentials are supplied.\n"
            )
    selected = active
    if not selected:
        if not watch:
            sys.stderr.write("No accounts have passwords configured; nothing to do.\n")
            return 0
        return _idle_watch_loop(heartbeat_file)

    show_header = len(selected) > 1

    if not watch:
        rc = 0
        for account in selected:
            if show_header:
                sys.stdout.write(f"=== account: {account.account_id} ===\n")
            if _cli._ingest_cycle(account.config, dry_run=dry_run) != 0:
                rc = 1
        return rc

    # -- startup probe: check each account before the first cycle ----------
    for account in selected:
        try:
            status, error = probe_account(account.config)
        except Exception as exc:
            status, error = "failed", str(exc)
        conn = _cli.init_db(account.config.db_path)
        try:
            write_account_health(conn, status=status, error=error, checked_at=utcnow())
        finally:
            conn.close()
        if status == "failed":
            sys.stderr.write(
                f"STARTUP: account '{account.account_id}' connection FAILED: {error}\n"
            )
        else:
            sys.stdout.write(f"STARTUP: account '{account.account_id}' connection OK\n")

    def _handle_sigterm(_sig: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        while True:
            # Reload accounts on each cycle so accounts added via the web UI
            # (or recovered from settings stores) are picked up without a
            # restart.  A failed reload keeps the last-known snapshot.
            try:
                fresh = _cli.load_accounts()
                fresh = merge_settings_store_accounts(fresh)
            except Exception:
                fresh = accounts

            if account_id is not None:
                try:
                    fresh_selected = [fresh.get(account_id)]
                except ConfigurationError:
                    sys.stderr.write(
                        f"Account {account_id!r} no longer present in the"
                        f" reloaded config; falling back to all"
                        f" {len(fresh.accounts)} configured accounts."
                        " Restart with --account to resume a"
                        "Restart with --account to resume a"
                        " single-account watch.\n"
                    )
                    fresh_selected = list(fresh.accounts)
            else:
                fresh_selected = list(fresh.accounts)

            # Filter to accounts with passwords.
            fresh_active = [
                a for a in fresh_selected if a.config.password.get_secret_value()
            ]
            if not fresh_active:
                # No active accounts — fall back to idle mode which will
                # re-check the config every 60 s for newly added credentials.
                return _idle_watch_loop(heartbeat_file)

            selected = fresh_active
            accounts = fresh
            show_header = len(selected) > 1
            interval_minutes = max(1, selected[0].config.ingest_interval_minutes)

            for account in selected:
                if show_header:
                    sys.stdout.write(f"=== account: {account.account_id} ===\n")
                try:
                    _cli._ingest_cycle(account.config, dry_run=dry_run)
                except Exception as exc:  # never let one bad cycle kill the loop
                    sys.stderr.write(f"Ingest cycle failed: {exc}\n")
            # touch heartbeat so Docker healthcheck can verify liveness
            if heartbeat_file is not None:
                try:
                    pathlib.Path(heartbeat_file).touch()
                except Exception as exc:
                    sys.stderr.write(f"Heartbeat write failed: {exc}\n")

            sys.stdout.write(f"Next ingest in {interval_minutes} min.\n")
            sys.stdout.flush()
            _cli.time.sleep(interval_minutes * 60)
    except (KeyboardInterrupt, SystemExit):  # fmt: skip
        sys.stdout.write("\nWatch stopped.\n")
        return 0
