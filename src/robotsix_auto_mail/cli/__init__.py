"""CLI for robotsix-auto-mail.

Entry point: ``main()``, exposed via console_scripts in pyproject.toml.
"""

from __future__ import annotations

import argparse
import sys
import time as time

from robotsix_auto_mail import __version__
from robotsix_auto_mail.cli.commands import (
    _cmd_auth_login,
    _cmd_board,
    _cmd_config_sync,
    _cmd_config_sync_set,
    _cmd_detect,
    _cmd_ingest,
    _cmd_migrate_config,
    _cmd_probe,
    _cmd_serve,
    _cmd_triage,
    _cmd_triage_rules,
    _cmd_triage_rules_set,
    _cmd_triage_set,
    _load_accounts_or_exit,
    _load_config_or_exit,
)
from robotsix_auto_mail.cli.commands import (
    _ingest_cycle as _ingest_cycle,
)
from robotsix_auto_mail.cli.config import (
    _prompt_hosts as _prompt_hosts,
)
from robotsix_auto_mail.cli.config import (
    _refine_manual as _refine_manual,
)
from robotsix_auto_mail.cli.config import (
    _refine_password as _refine_password,
)
from robotsix_auto_mail.cli.config import (
    _refine_with_llm as _refine_with_llm,
)
from robotsix_auto_mail.cli.config import (
    _verify_config as _verify_config,
)
from robotsix_auto_mail.cli.config import (
    _VerifyResult as _VerifyResult,
)
from robotsix_auto_mail.config import load_accounts as load_accounts
from robotsix_auto_mail.db import init_db as init_db
from robotsix_auto_mail.imap import ImapClient as ImapClient
from robotsix_auto_mail.logging import setup_logging
from robotsix_auto_mail.pipeline import ingest_mail as ingest_mail

__all__ = [
    "_VerifyResult",
    "_cmd_ingest",
    "_refine_manual",
    "_refine_password",
    "_refine_with_llm",
    "build_parser",
    "main",
]


def _add_account_arg(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--account`` selection flag to *parser*."""
    parser.add_argument(
        "--account",
        metavar="ID",
        default=None,
        help=(
            "Account id to operate on. Optional when only one account is "
            "configured; required when multiple exist."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="robotsix-auto-mail",
        description="Diagnose and operate on mail servers.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    sub = parser.add_subparsers(dest="command", title="subcommands")
    probe_parser = sub.add_parser(
        "probe", help="Probe IMAP and SMTP servers for diagnostics"
    )
    _add_account_arg(probe_parser)
    ingest_parser = sub.add_parser("ingest", help="Fetch new mail and store it locally")
    ingest_account_group = ingest_parser.add_mutually_exclusive_group()
    ingest_account_group.add_argument(
        "--account",
        metavar="ID",
        default=None,
        help=(
            "Account id to ingest. Optional when only one account is "
            "configured; without it every configured account is ingested."
        ),
    )
    ingest_account_group.add_argument(
        "--all-accounts",
        action="store_true",
        default=False,
        help="Ingest every configured account (the default when --account is omitted).",
    )
    ingest_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Fetch and parse messages without storing or advancing watermark",
    )
    ingest_parser.add_argument(
        "--watch",
        action="store_true",
        default=False,
        help=(
            "Keep running, ingesting on an interval (minutes) set by "
            "ingest.interval_minutes in the config (default 15)"
        ),
    )

    board_parser = sub.add_parser(
        "board", help="Display ingested mail in a read-only board view"
    )
    _add_account_arg(board_parser)

    serve_parser = sub.add_parser("serve", help="Start the web board server")
    _add_account_arg(serve_parser)
    serve_parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port to listen on (default: %(default)s)",
    )

    detect_parser = sub.add_parser(
        "detect",
        help="Auto-detect email provider settings via LLM and write config",
    )
    detect_parser.add_argument(
        "email",
        help="Email address to detect provider settings for",
    )
    detect_parser.add_argument(
        "--id",
        dest="id",
        default=None,
        metavar="ID",
        help=(
            "Account id for the detected account. Defaults to a sanitised id "
            "derived from the email address. Used as the multi-account "
            "`accounts:` entry id and the `.data/<id>/mail.db` store folder."
        ),
    )
    detect_parser.add_argument(
        "--password",
        default=None,
        help=(
            "Password to write into the config file. "
            "When omitted, prompts interactively."
        ),
    )
    detect_parser.add_argument(
        "--output",
        default="config/mail.local.yaml",
        help="Write mail config to this file path (default: %(default)s)",
    )
    detect_parser.add_argument(
        "--stdout",
        action="store_true",
        default=False,
        help="Print mail config to stdout instead of writing to file",
    )
    detect_parser.add_argument(
        "--no-verify",
        action="store_true",
        default=False,
        help=(
            "Skip the post-write IMAP/SMTP connection check. "
            "By default detect verifies the settings once a password is known."
        ),
    )

    config_sync_parser = sub.add_parser(
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
        "--provider",
        default=None,
        help="LLM backend name (e.g. openrouter-deepseek). Overrides "
        "LLM_PROVIDER env and config file.",
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

    triage_parser = sub.add_parser(
        "triage",
        help="Run the LLM inbox-triage agent and record advisory action "
        "statuses (does not move mail in the mailbox)",
    )
    _add_account_arg(triage_parser)
    triage_parser.add_argument(
        "--api-key",
        default=None,
        help="OpenRouter API key. Overrides LLM_API_KEY env and config file.",
    )
    triage_parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format for triage decisions (default: %(default)s).",
    )

    triage_set_parser = sub.add_parser(
        "triage-set",
        help="Record a user triage decision for a single message "
        "(advisory; does not move mail in the mailbox)",
    )
    _add_account_arg(triage_set_parser)
    triage_set_parser.add_argument(
        "message_id",
        help="Message-ID of the mail to triage.",
    )
    triage_set_parser.add_argument(
        "action",
        help="Triage action: INBOX, HUMAN_TRIAGE, TO_ARCHIVE, TO_DELETE, or TO_ANSWER.",
    )

    triage_rules_parser = sub.add_parser(
        "triage-rules",
        help="Propose deterministic triage rules from triage history and "
        "list the accepted (active) rules (advisory; no LLM call)",
    )
    _add_account_arg(triage_rules_parser)
    triage_rules_parser.add_argument(
        "--output-format",
        choices=["text", "json"],
        default="text",
        help="Output format for rule proposals (default: %(default)s).",
    )

    triage_rules_set_parser = sub.add_parser(
        "triage-rules-set",
        help="Accept or reject a proposed triage rule by fingerprint; "
        "accepted rules become active deterministic rules",
    )
    _add_account_arg(triage_rules_set_parser)
    triage_rules_set_parser.add_argument(
        "fingerprint",
        help="Fingerprint of the triage rule proposal.",
    )
    triage_rules_set_parser.add_argument(
        "state",
        help="New state: accepted or rejected.",
    )

    config_sync_set_parser = sub.add_parser(
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

    migrate_config_parser = sub.add_parser(
        "migrate-config",
        help="Convert a deprecated single-account config file into the "
        "multi-account `accounts:` shape (writes a .bak backup)",
    )
    migrate_config_parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Config file to migrate (default: the canonical config path).",
    )
    migrate_config_parser.add_argument(
        "--id",
        dest="id",
        default=None,
        metavar="ID",
        help="Account id for the migrated single account (default: 'default').",
    )
    migrate_config_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the migrated YAML to stdout without writing any file.",
    )

    auth_parser = sub.add_parser(
        "auth", help="Authenticate accounts (OAuth2 device-code login)"
    )
    auth_sub = auth_parser.add_subparsers(dest="auth_command", title="auth subcommands")
    login_parser = auth_sub.add_parser(
        "login", help="Run the OAuth2 device-code login for an account"
    )
    login_parser.add_argument(
        "--account",
        metavar="ID",
        default=None,
        help="Account id to authenticate.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args and dispatch to the appropriate subcommand handler.

    Returns 0 on success, 1 on failure.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # -- load configuration (env → YAML cascade) --
    from robotsix_auto_mail import config as _config
    from robotsix_auto_mail.tracing import init_langfuse_tracing

    try:
        _loaded_cfg = _config.load()
    except Exception:
        _loaded_cfg = None

    # -- configure logging from config (or defaults) --
    if _loaded_cfg is not None:
        setup_logging(
            level=_loaded_cfg.log_level,
            log_format=_loaded_cfg.log_format,
            log_file_dir=_loaded_cfg.log_file_dir,
        )
    else:
        setup_logging()

    # -- enable Langfuse tracing when credentials are configured --
    init_langfuse_tracing(_loaded_cfg)

    if args.command == "probe":
        return _cmd_probe(_load_config_or_exit(args.account))

    if args.command == "ingest":
        return _cmd_ingest(
            _load_accounts_or_exit(),
            account_id=args.account,
            all_accounts=args.all_accounts,
            dry_run=args.dry_run,
            watch=args.watch,
        )

    if args.command == "board":
        return _cmd_board(_load_config_or_exit(args.account))

    if args.command == "serve":
        from robotsix_auto_mail.config import ConfigurationError

        accounts = _load_accounts_or_exit()
        if args.account is not None:
            try:
                resolved = accounts.get(args.account).account_id
            except ConfigurationError as exc:
                sys.stderr.write(f"Error: {exc}\n")
                return 1
        else:
            resolved = accounts.default_account_id
        return _cmd_serve(accounts, default_account_id=resolved, port=args.port)

    if args.command == "detect":
        return _cmd_detect(args)

    if args.command == "migrate-config":
        return _cmd_migrate_config(args)

    if args.command == "config-sync":
        return _cmd_config_sync(args)

    if args.command == "triage":
        return _cmd_triage(args)

    if args.command == "triage-set":
        return _cmd_triage_set(args)

    if args.command == "triage-rules":
        return _cmd_triage_rules(args)

    if args.command == "triage-rules-set":
        return _cmd_triage_rules_set(args)

    if args.command == "config-sync-set":
        return _cmd_config_sync_set(args)

    if args.command == "auth":
        if args.auth_command == "login":
            return _cmd_auth_login(args)
        # No auth subcommand given — print the auth help and exit 1.
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                action.choices["auth"].print_help(sys.stderr)
                break
        return 1

    # No command given — print help and exit 1.
    parser.print_help(sys.stderr)
    return 1
