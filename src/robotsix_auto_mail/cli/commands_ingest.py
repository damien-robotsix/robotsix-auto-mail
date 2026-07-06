"""Ingest command handlers — extracted from commands.py."""

from __future__ import annotations

import contextlib
import pathlib
import signal
import sys

import robotsix_auto_mail.cli as _cli  # lgtm[py/unsafe-cyclic-import]
from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccountsConfig,
    MailConfig,
)
from robotsix_auto_mail.db.queries import write_account_health
from robotsix_auto_mail.health import probe_account, utcnow
from robotsix_auto_mail.pipeline import IngestResult, reconcile_records


def _ingest_cycle(config: MailConfig, *, dry_run: bool = False) -> int:
    """Run a single ingest pass: fetch, parse, store, and update watermark.

    Returns 0 when the pipeline runs (including per-message errors),
    or 1 for a fatal connection failure (ImapClient raise).
    """
    result: IngestResult | None = None
    conn = _cli.init_db(config.db_path)
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
    accounts: MailAccountsConfig,
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
    is processed in order, regardless of *all_accounts* (a single-account
    container yields exactly one account, so single-account usage is
    unchanged).  A per-account header is printed only when more than one
    account is processed.

    In watch mode it loops forever, running an ingest cycle for each selected
    account every interval.  A failed cycle is logged and the loop continues;
    Ctrl-C or SIGTERM exits cleanly with 0.
    """
    if account_id is not None:
        try:
            selected = [accounts.get(account_id)]
        except ConfigurationError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            sys.exit(1)
    else:
        selected = list(accounts.accounts)

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

    interval_minutes = max(1, selected[0].config.ingest_interval_minutes)
    sys.stdout.write(
        f"Watch mode: ingesting every {interval_minutes} min (Ctrl-C to stop).\n"
    )
    sys.stdout.flush()

    def _handle_sigterm(_sig: int, _frame: object) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_sigterm)

    try:
        while True:
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
    except KeyboardInterrupt, SystemExit:
        sys.stdout.write("\nWatch stopped.\n")
        return 0
