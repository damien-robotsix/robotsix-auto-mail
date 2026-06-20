"""Auth subcommand handler (OAuth2 device-code login)."""

from __future__ import annotations

import argparse
import sys

from robotsix_auto_mail.cli.commands import _load_accounts_or_exit
from robotsix_auto_mail.config import ConfigurationError


def _cmd_auth_login(args: argparse.Namespace) -> int:
    """Run the OAuth2 device-code login for an account, seeding the cache.

    Resolves the account's :class:`MailConfig` (honouring its per-account
    ``db_path``), requires an OAuth2 provider, and runs the device-code
    consent flow so subsequent silent token refresh works.  Returns 0 on
    success and non-zero on any error (unknown/ambiguous account, a
    non-OAuth2 account, missing ``msal``, or a device-flow failure) — no
    traceback.
    """
    from robotsix_auto_mail.oauth2 import (
        MICROSOFT_PROVIDER,
        cache_path_for,
        device_code_login,
    )

    accounts = _load_accounts_or_exit()
    if args.account is not None:
        try:
            account = accounts.get(args.account)
        except ConfigurationError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return 1
    elif len(accounts.accounts) == 1:
        account = accounts.accounts[0]
    else:
        sys.stderr.write(
            "Error: multiple accounts configured; pass --account <id>. "
            f"Available ids: {list(accounts.ids())!r}\n"
        )
        return 1

    config = account.config
    if config.oauth2_provider != MICROSOFT_PROVIDER:
        sys.stderr.write(
            "Error: `auth login` only applies to OAuth2 providers; account "
            f"{account.account_id!r} has no auth.oauth2_provider set.\n"
        )
        return 1

    try:
        device_code_login(config)
    except ConfigurationError as exc:
        sys.stderr.write(f"Error: {exc}\n")
        return 1
    except Exception as exc:  # device-flow error / user abort
        sys.stderr.write(f"Error: device-code login failed: {exc}\n")
        return 1

    sys.stdout.write(
        f"Device-code login complete. Token cache: {cache_path_for(config)}\n"
    )
    return 0
