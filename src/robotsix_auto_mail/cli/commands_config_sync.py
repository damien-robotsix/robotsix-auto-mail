"""Config-sync subcommand handlers."""

from __future__ import annotations

import argparse
import json
import sys

from robotsix_auto_mail.cli.commands import _load_config_or_exit, _print_header
from robotsix_auto_mail.db import init_db


def register_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    from robotsix_auto_mail.cli import _add_account_arg

    config_sync_parser = subparsers.add_parser(
        "config-sync",
        help="Run the LLM config-drift advisory agent (advisory only; "
        "does not replace the deterministic check_config_sync.py CI gate)",
    )
    _add_account_arg(config_sync_parser)
    config_sync_parser.add_argument(
        "--api-key",
        default=None,
        help="OpenRouter API key. Overrides LLM_API_KEY env and config file.",
    )
    config_sync_parser.add_argument(
        "--provider-model",
        default=None,
        help="LLM provider-model identifier (e.g. openrouter-deepseek). Overrides "
        "LLM_PROVIDER_MODEL env and config file.",
    )
    config_sync_parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format for drift findings (default: %(default)s).",
    )
    config_sync_parser.add_argument(
        "--dedup",
        action="store_true",
        default=False,
        help="Consult/update the dedup memory ledger so previously-seen "
        "findings are suppressed. Requires a loadable config (for db_path).",
    )

    config_sync_set_parser = subparsers.add_parser(
        "config-sync-set",
        help="Mark a config-drift finding accepted or rejected so it is "
        "suppressed by the dedup memory ledger",
    )
    _add_account_arg(config_sync_set_parser)
    config_sync_set_parser.add_argument(
        "fingerprint",
        help="Fingerprint of the config-drift finding.",
    )
    config_sync_set_parser.add_argument(
        "state",
        help="Ledger state: pending, accepted, or rejected.",
    )


def _cmd_config_sync(args: argparse.Namespace) -> int:
    """Run the config-drift advisory agent and render its proposals.

    This is an advisory tool, not a CI gate: a successful run returns 0
    even when drift proposals are found.  Returns 1 only on error (missing
    pydantic_ai, ConfigSyncError, missing API key).
    """
    try:
        from robotsix_auto_mail.config.config_sync_agent import (
            ConfigSyncError,
            run_config_sync_agent,
        )
    except ImportError:
        sys.stderr.write(
            "The 'config-sync' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    # Resolve the dedup connection only when --dedup is requested; like
    # detect, the advisory tool should not require a full mail config to run.
    conn = None
    if args.dedup:
        config = _load_config_or_exit(args.account)
        conn = init_db(config.db_path)

    try:
        result = run_config_sync_agent(
            api_key=args.api_key, provider_model=args.provider_model, conn=conn
        )
    except ConfigSyncError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1

    if args.output_format == "json":
        sys.stdout.write(json.dumps(result.model_dump(), indent=2) + "\n")
        # Advisory tool: a non-empty result is informational, not a gate.
        return 0

    _print_header(sys.stdout, "Config Drift Advisory")
    if not result.proposals:
        sys.stdout.write("No config drift detected.\n")
        # Advisory tool: a non-empty result is informational, not a gate.
        return 0

    for proposal in result.proposals:
        sys.stdout.write(f"\n{proposal.title}\n")
        sys.stdout.write(f"  confidence: {proposal.confidence}\n")
        field = proposal.affected_field if proposal.affected_field else "(none)"
        sys.stdout.write(f"  affected field: {field}\n")
        sys.stdout.write(f"\n{proposal.body}\n")

    # Advisory tool: a non-empty result is informational, not a gate.
    return 0


def _cmd_config_sync_set(args: argparse.Namespace) -> int:
    """Record a user decision for a single config-drift finding.

    Returns 0 on success, 1 when the fingerprint is unknown or the state is
    invalid.
    """
    try:
        from robotsix_auto_mail.config.config_sync_agent import (
            _VALID_LEDGER_STATES,
            ConfigSyncError,
            set_finding_state,
        )
    except ImportError:
        sys.stderr.write(
            "The 'config-sync-set' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    if args.state not in _VALID_LEDGER_STATES:
        sys.stderr.write(
            f"Error: invalid state {args.state!r}. "
            f"Must be one of {sorted(_VALID_LEDGER_STATES)}\n"
        )
        return 1

    config = _load_config_or_exit(args.account)
    conn = init_db(config.db_path)
    try:
        set_finding_state(conn, args.fingerprint, args.state)
    except ConfigSyncError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    finally:
        conn.close()

    sys.stdout.write(
        f"Recorded config-drift finding state: {args.fingerprint} -> {args.state}\n"
    )
    return 0
