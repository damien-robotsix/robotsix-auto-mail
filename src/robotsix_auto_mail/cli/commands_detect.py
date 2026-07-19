"""Detect command handler — extracted from commands.py."""

from __future__ import annotations

import argparse
import json
import sys

from robotsix_auto_mail.cli.config import (
    _account_id_from_email,
    _detect_settings,
    _get_password,
    _verify_and_refine,
)
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap.client import _IMAP4_ERROR
from robotsix_auto_mail.imap.errors import ImapError
from robotsix_auto_mail.smtp import _SMTP_EXCEPTION, SmtpError


def register_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register the ``detect`` subcommand and its arguments.

    The subcommand auto-detects email provider settings via LLM, verifies
    them by connecting to IMAP and SMTP, and prints a JSON diagnostic report.
    No config file is written — the operator copies the report values into
    the deploy Configure panel.
    """
    parser = subparsers.add_parser(
        "detect",
        help=(
            "Auto-detect email provider settings via LLM and print a diagnostic report"
        ),
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
            "derived from the email address."
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
        default="config/config.json",
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
            "Skip the post-detection IMAP/SMTP connection check. "
            "By default detect verifies the settings once a password is known."
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
    """Run the detect subcommand: auto-detect provider settings, verify them
    by connecting, and print a JSON diagnostic report.  No config file is
    written.

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
        from robotsix_auto_mail.config.loader import _dump_config_json

        json_text = _dump_config_json(container)
        sys.stdout.write(json_text)  # lgtm[py/clear-text-logging-sensitive-data]
        return 0

    rc, config = _verify_and_refine(
        provider,
        email=args.email,
        api_key=api_key,
        llm_provider_model=llm_provider_model_str,
        mx_hosts=mx_hosts,
        password=password,
        password_from_args=args.password,
        no_verify=args.no_verify,
        account_id=account_id,
        label=label,
        provider_to_config=provider_to_config,
        detect_provider=detect_provider,
        _detection_error=DetectionError,
        microsoft=microsoft,
        oauth2_client_id=args.oauth2_client_id,
        oauth2_tenant=args.oauth2_tenant,
        app_password=args.app_password,
    )

    if config is None:
        return rc

    # Print the diagnostic report to stdout as JSON.
    # verified is True only when verification actually ran and passed.
    verified = rc == 0 and not args.no_verify
    imap_capabilities, smtp_features = _probe_capabilities(config, verified)
    report = _build_detect_report(
        imap_host=config.imap_host,
        imap_port=config.imap_port,
        imap_tls_mode=config.imap_tls_mode,
        smtp_host=config.smtp_host,
        smtp_port=config.smtp_port,
        smtp_tls_mode=config.smtp_tls_mode,
        username=config.username,
        oauth2_client_id=config.oauth2_client_id,
        oauth2_tenant=config.oauth2_tenant,
        oauth2_provider=config.oauth2_provider,
        verified=verified,
        imap_capabilities=imap_capabilities,
        smtp_features=smtp_features,
    )
    # Drop the config reference before printing so taint-tracking tools
    # (CodeQL) cannot trace config.password into the stdout write below.
    del config
    _print_detect_report(report)
    return rc


def _probe_capabilities(
    config: MailConfig, verified: bool
) -> tuple[list[str], dict[str, str]]:
    """Collect IMAP/SMTP capability metadata for the diagnostic report.

    Returns empty collections when *verified* is ``False`` or a probe fails.
    """
    from robotsix_auto_mail.imap import ImapClient
    from robotsix_auto_mail.smtp import SmtpClient

    if not verified:
        return [], {}

    imap_capabilities: list[str] = []
    smtp_features: dict[str, str] = {}
    try:
        with ImapClient(config) as imap:
            imap_capabilities = list(imap.capabilities)
    except OSError, _IMAP4_ERROR, ImapError:
        # Best-effort capability probe; failures are non-critical and ignored.
        pass
    try:
        with SmtpClient(config) as smtp:
            smtp_features = dict(smtp.esmtp_features)
    except OSError, _SMTP_EXCEPTION, SmtpError:
        # Best-effort capability probe; failures are non-critical and ignored.
        pass
    return imap_capabilities, smtp_features


def _build_detect_report(
    *,
    imap_host: str,
    imap_port: int,
    imap_tls_mode: str,
    smtp_host: str,
    smtp_port: int,
    smtp_tls_mode: str,
    username: str,
    oauth2_client_id: str,
    oauth2_tenant: str,
    oauth2_provider: str,
    verified: bool,
    imap_capabilities: list[str],
    smtp_features: dict[str, str],
) -> dict[str, object]:
    """Build a JSON-safe diagnostic report dictionary.

    The report uses keys matching the config schema so the operator can
    copy-paste values into the deploy Configure panel.  Passwords are
    never included.
    """
    report: dict[str, object] = {
        "imap_host": imap_host,
        "imap_port": imap_port,
        "imap_tls_mode": imap_tls_mode,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        "smtp_tls_mode": smtp_tls_mode,
        "username": username,
    }
    if oauth2_client_id:
        report["oauth2_client_id"] = oauth2_client_id
    if oauth2_tenant:
        report["oauth2_tenant"] = oauth2_tenant
    if oauth2_provider:
        report["oauth2_provider"] = oauth2_provider

    report["imap_capabilities"] = imap_capabilities
    report["smtp_features"] = smtp_features
    report["login_ok"] = verified
    return report


def _print_detect_report(report: dict[str, object]) -> None:
    """Print the diagnostic *report* as JSON to stdout.

    The *report* must already exclude sensitive fields such as passwords.
    """
    sys.stdout.write(json.dumps(report, indent=2))
    sys.stdout.write("\n")

    # Paste instructions.
    sys.stderr.write(
        "\n---\n"
        "Copy the settings above into the deploy Configure panel.\n"
        "Enter the password into the masked write-only password field.\n"
    )
