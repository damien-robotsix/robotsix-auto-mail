"""Triage subcommand handlers and stale-state cleanup."""

from __future__ import annotations

import argparse
import json
import sys

from robotsix_auto_mail.cli.commands import _load_config_or_exit, _print_header
from robotsix_auto_mail.config import MailAccountsConfig
from robotsix_auto_mail.db import get_record_by_message_id, init_db


def _cmd_triage(args: argparse.Namespace) -> int:
    """Run the inbox-triage agent and render the recorded decisions.

    This is an advisory tool, not a CI gate: a successful run returns 0 even
    when triage decisions are produced.  Returns 1 only on error (missing
    pydantic_ai, TriageError).
    """
    try:
        from robotsix_auto_mail.triage import TriageError, run_triage_agent
    except ImportError:
        sys.stderr.write(
            "The 'triage' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    config = _load_config_or_exit(args.account)
    conn = init_db(config.db_path)
    try:
        decisions = run_triage_agent(
            conn,
            api_key=args.api_key,
            provider=config.llm_provider,
            user_email=config.username,
        )
    except TriageError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    finally:
        conn.close()

    if args.output_format == "json":
        payload = [d.model_dump() for d in decisions]
        sys.stdout.write(json.dumps(payload, indent=2) + "\n")
        return 0

    _print_header(sys.stdout, "Inbox Triage")
    if not decisions:
        sys.stdout.write("No inbox mail to triage.\n")
        return 0

    for decision in decisions:
        sys.stdout.write(f"\n{decision.message_id}\n")
        sys.stdout.write(f"  action: {decision.action}\n")
        sys.stdout.write(f"  confidence: {decision.confidence}\n")
        reason = decision.reason if decision.reason else "(none)"
        sys.stdout.write(f"  reason: {reason}\n")

    return 0


def _cmd_triage_set(args: argparse.Namespace) -> int:
    """Record a user triage decision for a single message.

    Returns 0 on success, 1 when the message_id is unknown or the action is
    invalid.
    """
    try:
        from robotsix_auto_mail.triage import (
            VALID_TRIAGE_ACTIONS,
            TriageError,
            record_human_decision,
            set_triage_decision,
        )
    except ImportError:
        sys.stderr.write(
            "The 'triage-set' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    if args.action not in VALID_TRIAGE_ACTIONS:
        sys.stderr.write(
            f"Error: invalid action {args.action!r}. "
            f"Must be one of {sorted(VALID_TRIAGE_ACTIONS)}\n"
        )
        return 1

    config = _load_config_or_exit(args.account)
    conn = init_db(config.db_path)
    try:
        if get_record_by_message_id(conn, args.message_id) is None:
            sys.stderr.write(f"Error: no mail with message_id {args.message_id!r}\n")
            return 1
        try:
            set_triage_decision(conn, args.message_id, args.action, source="user")
            record_human_decision(conn, args.message_id, args.action)
        except TriageError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return 1
    finally:
        conn.close()

    sys.stdout.write(
        f"Recorded user triage decision: {args.message_id} -> {args.action}\n"
    )
    return 0


def _clear_stale_triage_state(accounts: MailAccountsConfig) -> None:
    """Reset any orphaned background-op watermarks to idle.

    Called once at board-server startup. After a fresh process start
    there can be no live worker thread, so any 'running' flag (the
    ``triage_run:state`` triage watermark or a non-idle ``batch_op:state``
    batch-delete/archive watermark) is a leftover from a SIGKILL'd
    container and is safe to clear.
    """
    from robotsix_auto_mail.db import get_watermark, set_watermark

    for acct in accounts.accounts:
        db_path = acct.config.db_path
        try:
            conn = init_db(db_path, skip_migrations=True)
            try:
                if get_watermark(conn, "triage_run:state") == "running":
                    set_watermark(conn, "triage_run:state", "idle")
                if get_watermark(conn, "reconcile:state") == "running":
                    set_watermark(conn, "reconcile:state", "idle")
                batch_state = get_watermark(conn, "batch_op:state")
                if batch_state is not None and batch_state != "idle":
                    set_watermark(conn, "batch_op:state", "idle")
            finally:
                conn.close()
        except Exception:  # noqa: S112  # nosec B112
            # Best-effort: a bad/unopenable account DB must never abort the
            # boot loop or crash the server.
            continue
