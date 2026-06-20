"""Per-command handlers and local rendering helpers for the CLI."""

from __future__ import annotations

import sys
from typing import TextIO

import robotsix_auto_mail.cli as _cli
from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccountsConfig,
    MailConfig,
)


def _print_header(file: TextIO, title: str, width: int = 60, char: str = "-") -> None:
    file.write(f"\n{title}\n{char * width}\n")


def _load_accounts_or_exit() -> MailAccountsConfig:
    """Load the accounts container, or print to stderr and exit 1 on failure."""
    try:
        return _cli.load_accounts()
    except Exception as exc:
        sys.stderr.write(f"Error loading configuration: {exc}\n")
        sys.exit(1)


def _load_config_or_exit(account_id: str | None = None) -> MailConfig:
    """Select one account's :class:`MailConfig`, or exit 1 on failure.

    With *account_id* given, returns that account's config (exiting 1 with the
    valid ids on an unknown id).  With *account_id* ``None`` the **default
    account** is returned (``default_account_id``, which falls back to the
    first account when ``default_account`` is unset), so single-mailbox usage
    never needs ``--account`` and multi-account usage selects the default.
    """
    accounts = _load_accounts_or_exit()
    if account_id is not None:
        try:
            return accounts.get(account_id).config
        except ConfigurationError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            sys.exit(1)
    return accounts.default.config
