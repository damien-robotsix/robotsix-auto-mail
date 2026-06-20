"""Detect command handler — extracted from commands.py."""

from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

from robotsix_auto_mail.cli.config import (
    _account_id_from_email,
    _detect_settings,
    _existing_account_ids,
    _get_password,
    _verify_and_refine,
)
from robotsix_auto_mail.config import (
    MailAccount,
    render_accounts_yaml,
)


def _cmd_detect(args: argparse.Namespace) -> int:
    """Run the detect subcommand: auto-detect provider settings, write the
    config, and verify it by connecting — refining with autoconfig, the LLM,
    and finally a manual prompt when the servers cannot be reached.
    Returns 0 on success, 1 on any error.
    """
    try:
        from robotsix_auto_mail.detect import (
            DetectionError,
            autoconfig_lookup,
            detect_provider,
            is_microsoft_provider,
            mx_lookup,
            provider_from_mx,
            provider_to_config,
        )
    except ImportError:
        sys.stderr.write(
            "The 'detect' command requires the pydantic-ai package. "
            "Install it with: pip install robotsix-auto-mail[dev]\n"
        )
        return 1

    from robotsix_auto_mail.config import load_llm, load_llm_provider

    account_id = args.id or _account_id_from_email(args.email)
    label = args.email

    api_key = load_llm()
    llm_provider_str = load_llm_provider()
    provider, mx_hosts = _detect_settings(
        args.email,
        api_key,
        llm_provider_str,
        autoconfig_lookup,
        mx_lookup,
        provider_from_mx,
        detect_provider,
        DetectionError,
    )
    if provider is None:
        return 1
    microsoft = is_microsoft_provider(provider)
    # Microsoft 365 rejects password auth; it uses MSAL-managed XOAUTH2, so we
    # never prompt for or write a password for these accounts.
    if microsoft:
        password: str | None = None
    else:
        password = _get_password(args)
        if password is None:
            return 1
    if args.stdout:
        config = dataclasses.replace(
            provider_to_config(
                provider,
                args.email,
                # lgtm[py/hardcoded-credentials]
                password="",  # nosec B106 - intentionally omitted from stdout
            ),
            db_path=f".data/{account_id}/mail.db",
        )
        if microsoft:
            sys.stderr.write(
                f"# Detected Microsoft 365 settings for {args.email} — "
                "OAuth2 (XOAUTH2); no password is used.\n"
                "# Save this as config/mail.local.yaml, then run:\n"
                f"#   robotsix-auto-mail auth login --account {account_id}\n"
                "# to complete the device-code consent and seed the token "
                "cache.\n"
            )
        else:
            sys.stderr.write(
                f"# Detected settings for {args.email} — verify before using.\n"
                "# The password was intentionally omitted: fill in auth.password "
                "or set the MAIL_PASSWORD env var before use.\n"
                "# Save this as config/mail.local.yaml.\n"
            )
        account = MailAccount(account_id=account_id, config=config, label=label)
        sys.stdout.write(render_accounts_yaml([account], account_id))
        return 0

    output_path = Path(args.output)
    if account_id in _existing_account_ids(output_path):
        sys.stderr.write(
            f"Error: account {account_id!r} already exists in {output_path}. "
            "Pass --id <new-id> to add a different account, or edit the file "
            "directly.\n"
        )
        return 1
    return _verify_and_refine(
        provider,
        email=args.email,
        api_key=api_key,
        llm_provider=llm_provider_str,
        mx_hosts=mx_hosts,
        output_path=output_path,
        password=password,
        password_from_args=args.password,
        no_verify=args.no_verify,
        account_id=account_id,
        label=label,
        provider_to_config=provider_to_config,
        detect_provider=detect_provider,
        _detection_error=DetectionError,
        microsoft=microsoft,
    )
