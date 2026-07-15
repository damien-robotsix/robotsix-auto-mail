"""Detect command handler — extracted from commands.py."""

from __future__ import annotations

import argparse
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
    MailAccountsConfig,
)


def register_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    parser = subparsers.add_parser(
        "detect",
        help="Auto-detect email provider settings via LLM and write config",
    )
    parser.add_argument(
        "email",
        help="Email address to detect provider settings for",
    )
    parser.add_argument(
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
    parser.add_argument(
        "--password",
        default=None,
        help=(
            "Password to write into the config file. "
            "When omitted, prompts interactively."
        ),
    )
    parser.add_argument(
        "--output",
        default="config/mail.local.yaml",
        help="Write mail config to this file path (default: %(default)s)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        default=False,
        help="Print mail config to stdout instead of writing to file",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        default=False,
        help=(
            "Skip the post-write IMAP/SMTP connection check. "
            "By default detect verifies the settings once a password is known."
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=False,
        help=(
            "When the account id already exists in the output file, update its "
            "transport settings (imap/smtp host, port, tls_mode) in place instead "
            "of erroring. Other account fields (label, username, password, "
            "db_path, archive, triage, calendar, oauth2 settings) are preserved "
            "from the existing entry unless explicitly supplied on the command line."
        ),
    )
    parser.add_argument(
        "--app-password",
        action="store_true",
        default=False,
        help=(
            "Use password/basic auth even for Microsoft-hosted accounts. "
            "Mutually exclusive with --oauth2-client-id / --oauth2-tenant. "
            "WARNING: OAuth2 is strongly preferred; basic auth may be disabled "
            "for your tenant."
        ),
    )
    parser.add_argument(
        "--oauth2-client-id",
        dest="oauth2_client_id",
        default="",
        metavar="UUID",
        help=(
            "Azure app-registration client ID for Microsoft 365 OAuth2. "
            "Defaults to the Thunderbird public client when omitted."
        ),
    )
    parser.add_argument(
        "--oauth2-tenant",
        dest="oauth2_tenant",
        default="",
        metavar="TENANT",
        help=(
            "Azure AD tenant for Microsoft 365 OAuth2: 'organizations', "
            "'common', or a tenant GUID/domain (default: 'organizations')."
        ),
    )


def _cmd_detect(args: argparse.Namespace) -> int:
    """Run the detect subcommand: auto-detect provider settings, write the
    config, and verify it by connecting — refining with autoconfig, the LLM,
    and finally a manual prompt when the servers cannot be reached.
    Returns 0 on success, 1 on any error.
    """
    try:
        from robotsix_auto_mail.config.detect import (
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
            "Install it with: uv sync --extra llm (from the robotsix-auto-mail repo)\n"
        )
        return 1

    import os as _os

    from robotsix_auto_mail.config import (
        resolve_llm_api_key,
        resolve_llm_provider_model,
    )

    account_id = args.id or _account_id_from_email(args.email)
    label = args.email

    # Reject --app-password + --oauth2-* early (before the detection ladder)
    # so the user gets an immediate error instead of paying for an LLM call.
    if args.app_password and (args.oauth2_client_id or args.oauth2_tenant):
        sys.stderr.write(
            "Error: --app-password is mutually exclusive with "
            "--oauth2-client-id / --oauth2-tenant.\n"
        )
        return 1

    api_key = _os.environ.get("LLM_API_KEY") or resolve_llm_api_key(
        None, raise_on_missing=False
    )
    llm_provider_model_str = resolve_llm_provider_model(None)
    provider, mx_hosts = _detect_settings(
        args.email,
        api_key,
        llm_provider_model_str,
        autoconfig_lookup,
        mx_lookup,
        provider_from_mx,
        detect_provider,
        DetectionError,
    )
    if provider is None:
        return 1
    microsoft = is_microsoft_provider(provider)
    if microsoft and args.app_password:
        sys.stderr.write(
            "Warning: --app-password bypasses OAuth2; basic auth may be"
            " disabled for your tenant.\n"
        )
        microsoft = False
    # Microsoft 365 rejects password auth; it uses MSAL-managed XOAUTH2, so we
    # never prompt for or write a password for these accounts.
    if microsoft:
        password: str | None = None
    else:
        password = _get_password(args)
        if password is None:
            return 1
    if args.stdout:
        config = provider_to_config(
            provider,
            args.email,
            # lgtm[py/clear-text-storage-sensitive-data]
            password="",  # nosec B106 - intentionally omitted from stdout
        ).model_copy(update={"db_path": f".data/{account_id}/mail.db"})
        if args.app_password and config.oauth2_provider:
            # provider_to_config sets oauth2_provider="microsoft" for
            # Microsoft hosts unconditionally; --app-password must clear it
            # in the stdout path because _build() is never reached here.
            config = config.model_copy(update={"oauth2_provider": ""})
        if microsoft:
            sys.stderr.write(
                f"# Detected Microsoft 365 settings for {args.email} — "
                "OAuth2 (XOAUTH2); no password is used.\n"
                "# Save this as config/config.json, then run:\n"
                f"#   robotsix-auto-mail auth login --account {account_id}\n"
                "# to complete the device-code consent and seed the token "
                "cache.\n"
            )
        else:
            sys.stderr.write(
                f"# Detected settings for {args.email} — verify before using.\n"
                "# The password was intentionally omitted: fill in auth.password "
                "before use.\n"
                "# Save this as config/config.json.\n"
            )
        account = MailAccount(account_id=account_id, config=config, label=label)
        container = MailAccountsConfig(
            accounts=[account], default_account_id=account_id
        )
        # lgtm[py/clear-text-storage-sensitive-data]
        sys.stdout.write(container.model_dump_json(indent=2))
        return 0

    output_path = Path(args.output)
    if account_id in _existing_account_ids(output_path) and not args.overwrite:
        sys.stderr.write(
            f"Error: account {account_id!r} already exists in {output_path}. "
            "Pass --id <new-id> to add a different account, --overwrite to "
            "update the existing entry, or edit the file directly.\n"
        )
        return 1
    return _verify_and_refine(
        provider,
        email=args.email,
        api_key=api_key,
        llm_provider_model=llm_provider_model_str,
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
        overwrite=args.overwrite,
        oauth2_client_id=args.oauth2_client_id,
        oauth2_tenant=args.oauth2_tenant,
        app_password=args.app_password,
    )
