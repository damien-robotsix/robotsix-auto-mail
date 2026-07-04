"""CLI for robotsix-auto-mail.

Entry point: ``main()``, exposed via console_scripts in pyproject.toml.
"""

from __future__ import annotations

import argparse
import sys
import time as time

from robotsix_auto_mail import __version__, setup_observability
from robotsix_auto_mail.cli.commands import (  # lgtm[py/unsafe-cyclic-import]
    _load_accounts_or_exit,
    _load_config_or_exit,
)
from robotsix_auto_mail.cli.commands_auth import (
    _cmd_auth_login,
)
from robotsix_auto_mail.cli.commands_board import (
    _cmd_board,
)
from robotsix_auto_mail.cli.commands_config_sync import (
    _cmd_config_sync,
    _cmd_config_sync_set,
)
from robotsix_auto_mail.cli.commands_detect import (
    _cmd_detect,
)
from robotsix_auto_mail.cli.commands_ingest import (
    _cmd_ingest,
)
from robotsix_auto_mail.cli.commands_ingest import (
    _ingest_cycle as _ingest_cycle,
)
from robotsix_auto_mail.cli.commands_probe import (
    _cmd_probe,
)
from robotsix_auto_mail.cli.commands_serve import (
    _cmd_serve,
)
from robotsix_auto_mail.cli.commands_triage import (
    _cmd_triage,
    _cmd_triage_set,
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
    """Build the top-level argument parser with subcommands.

    Each subcommand's arguments are registered by a ``register_subparser``
    function in the corresponding ``commands_*.py`` module so the argument
    definitions live alongside their handlers.
    """
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
    from .commands_probe import register_subparser as _r1; _r1(sub)
    from .commands_ingest import register_subparser as _r2; _r2(sub)
    from .commands_board import register_subparser as _r3; _r3(sub)
    from .commands_serve import register_subparser as _r4; _r4(sub)
    from .commands_detect import register_subparser as _r5; _r5(sub)
    from .commands_config_sync import register_subparser as _r6; _r6(sub)
    from .commands_triage import register_subparser as _r7; _r7(sub)
    from .commands_auth import register_subparser as _r8; _r8(sub)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args and dispatch to the appropriate subcommand handler.

    Returns 0 on success, 1 on failure.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    # -- load configuration --
    from robotsix_auto_mail import config as _config

    try:
        _loaded_cfg = _config.load()
    except Exception:
        _loaded_cfg = None

    # -- configure logging + Langfuse tracing from config (or defaults) --
    setup_observability(_loaded_cfg)

    if args.command == "probe":
        return _cmd_probe(_load_config_or_exit(args.account))

    if args.command == "ingest":
        return _cmd_ingest(
            _load_accounts_or_exit(),
            account_id=args.account,
            all_accounts=args.all_accounts,
            dry_run=args.dry_run,
            watch=args.watch,
            heartbeat_file=args.heartbeat_file,
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
        return _cmd_serve(
            accounts,
            default_account_id=resolved,
            port=args.port,
            host=args.host,
        )

    if args.command == "detect":
        return _cmd_detect(args)

    if args.command == "config-sync":
        return _cmd_config_sync(args)

    if args.command == "triage":
        return _cmd_triage(args)

    if args.command == "triage-set":
        return _cmd_triage_set(args)

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
