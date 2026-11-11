"""Config verify/refine/detect helpers for the CLI.

Leaf module: imported by ``cli.commands`` and ``cli.__init__``; imports
nothing from either of those.
"""

from __future__ import annotations

import argparse
import dataclasses
import getpass
import logging
import re
import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import SecretStr

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import ImapAuthError, ImapClient, ImapError
from robotsix_auto_mail.outbound.smtp import (
    SmtpAuthError,
    SmtpClient,
    SmtpError,
)

if TYPE_CHECKING:
    from robotsix_auto_mail.detect import MailProvider

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class _VerifyResult:
    """Outcome of a quiet IMAP + SMTP connection check.

    ``*_auth`` is True when the server was reachable but authentication
    failed (i.e. the host is right but the password is wrong).
    """

    imap_ok: bool
    smtp_ok: bool
    imap_error: str = ""
    smtp_error: str = ""
    imap_auth: bool = False
    smtp_auth: bool = False

    @property
    def ok(self) -> bool:
        """True when both IMAP and SMTP connection checks succeeded."""
        return self.imap_ok and self.smtp_ok

    @property
    def host_problem(self) -> bool:
        """A failure that is NOT an auth failure (wrong host/port/TLS)."""
        imap_host_bad = not self.imap_ok and not self.imap_auth
        smtp_host_bad = not self.smtp_ok and not self.smtp_auth
        return imap_host_bad or smtp_host_bad

    @property
    def only_auth_problem(self) -> bool:
        """Reachable everywhere, but at least one authentication failed."""
        return not self.ok and not self.host_problem


def _verify_config(config: MailConfig) -> _VerifyResult:
    """Attempt authenticated IMAP and SMTP connections, quietly.

    Returns a :class:`_VerifyResult` categorising each side as ok, an auth
    failure, or a connection/TLS failure.  Prints nothing.
    """
    imap_ok = smtp_ok = False
    imap_error = smtp_error = ""
    imap_auth = smtp_auth = False

    try:
        with ImapClient(config) as imap:
            imap.list_folders()
        imap_ok = True
    except ImapAuthError as exc:
        imap_error, imap_auth = str(exc), True
    except ImapError as exc:
        imap_error = str(exc)

    try:
        with SmtpClient(config):
            pass
        smtp_ok = True
    except SmtpAuthError as exc:
        smtp_error, smtp_auth = str(exc), True
    except SmtpError as exc:
        smtp_error = str(exc)

    return _VerifyResult(
        imap_ok=imap_ok,
        smtp_ok=smtp_ok,
        imap_error=imap_error,
        smtp_error=smtp_error,
        imap_auth=imap_auth,
        smtp_auth=smtp_auth,
    )


def _report_verify_result(result: _VerifyResult) -> None:
    """Print a one-line-per-server summary of a verification attempt."""
    for label, ok, auth, err in (
        ("IMAP", result.imap_ok, result.imap_auth, result.imap_error),
        ("SMTP", result.smtp_ok, result.smtp_auth, result.smtp_error),
    ):
        if ok:
            sys.stderr.write(f"  {label}: ok\n")
        else:
            kind = "auth" if auth else "connection"
            sys.stderr.write(f"  {label}: {kind} failed — {err}\n")


def _prompt_hosts(config: MailConfig, result: _VerifyResult) -> MailConfig | None:
    """Prompt the user for the host(s) that failed to connect.

    Returns an updated config, or ``None`` if the user supplied nothing new
    (or input is unavailable), so the caller can stop instead of looping.
    """
    imap_host = config.imap_host
    smtp_host = config.smtp_host
    changed = False
    try:
        if not result.imap_ok and not result.imap_auth:
            ans = input(f"Enter IMAP host [{config.imap_host}]: ").strip()
            if ans:
                imap_host, changed = ans, True
        if not result.smtp_ok and not result.smtp_auth:
            ans = input(f"Enter SMTP host [{config.smtp_host}]: ").strip()
            if ans:
                smtp_host, changed = ans, True
    except (EOFError, KeyboardInterrupt):  # fmt: skip
        return None
    if not changed:
        return None
    return config.model_copy(update={"imap_host": imap_host, "smtp_host": smtp_host})


def _account_id_from_email(email: str) -> str:
    """Derive a filesystem/URL-safe account id from an email address.

    The local part and domain are joined and any character outside
    ``[A-Za-z0-9._-]`` is collapsed to ``-`` (matching the account-id charset
    enforced by :class:`MailAccount`).
    """
    local, _, domain = email.partition("@")
    base = f"{local}-{domain}" if domain else local
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-._")
    if not cleaned:
        # Edge case: email yields no usable characters — derive from the
        # original string with maximal sanitisation.
        cleaned = re.sub(r"[^A-Za-z0-9]", "-", email).strip("-") or "unknown"
    return cleaned


def _get_password(args: argparse.Namespace) -> str | None:
    """Get password from args or interactive prompt.

    Returns the password, or ``None`` if the user cancelled (EOF / KeyboardInterrupt).
    """
    password: str | None = args.password
    if password is None:
        try:
            password = getpass.getpass("Email password: ")
        except (EOFError, KeyboardInterrupt):  # fmt: skip
            sys.stderr.write("\nDetection cancelled.\n")
            return None
    return password


def _detect_settings(
    email: str,
    autoconfig_lookup: Callable[[str], MailProvider | None],
    mx_lookup: Callable[[str], list[str]],
    provider_from_mx: Callable[[list[str]], MailProvider | None],
) -> MailProvider | None:
    """Run the provider-detection ladder for *email*.

    Tries, in order:
    1. ``autoconfig_lookup`` (Thunderbird/Outlook-style autodiscovery)
    2. MX-record lookup → ``provider_from_mx``

    Prints progress messages to stderr.  Returns the detected
    ``MailProvider``, or ``None`` when neither step resolves (an error
    pointing at manual configuration is printed to stderr).
    """
    sys.stderr.write(f"Detecting settings for {email}…\n")
    provider = autoconfig_lookup(email)
    if provider is not None:
        sys.stderr.write(
            f"  autoconfig: imap={provider.imap_host} smtp={provider.smtp_host}\n"
        )
        return provider
    sys.stderr.write("  autoconfig: no match — checking MX records…\n")
    mx_hosts = mx_lookup(email)
    if mx_hosts:
        sys.stderr.write(f"  MX: {', '.join(mx_hosts[:3])}\n")
    provider = provider_from_mx(mx_hosts)
    if provider is None:
        sys.stderr.write(
            f"Error: could not auto-detect settings for {email} (no autoconfig "
            "and no known provider for the MX records). Configure the account "
            "manually in config/config.json.\n"
        )
        return None
    sys.stderr.write(
        f"  MX provider: imap={provider.imap_host} smtp={provider.smtp_host}\n"
    )
    return provider


@dataclasses.dataclass(frozen=True)
class _RefineOutcome:
    """Result of one refinement strategy: a rebuilt config, if any.

    ``config`` is ``None`` when the strategy produced no new config (the
    user cancelled).
    """

    config: MailConfig | None = None


def _refine_password(
    build: Callable[[MailProvider, str | None], MailConfig],
    provider: MailProvider,
) -> _RefineOutcome:
    """Re-prompt the password after a reachable-but-rejected auth failure."""
    sys.stderr.write("The server is reachable but the password was rejected.\n")
    try:
        new_pw = getpass.getpass("Re-enter email password: ")
    except (EOFError, KeyboardInterrupt):  # fmt: skip
        return _RefineOutcome()
    if not new_pw:
        return _RefineOutcome()
    return _RefineOutcome(config=build(provider, new_pw))


def _refine_manual(config: MailConfig, result: _VerifyResult) -> _RefineOutcome:
    """Prompt the user for the failing host(s) as a last resort."""
    from robotsix_auto_mail import cli

    sys.stderr.write(
        "Could not auto-detect a working host — please enter it manually.\n"
    )
    updated = cli._prompt_hosts(config, result)
    if updated is None:
        return _RefineOutcome()
    return _RefineOutcome(config=updated)


def _verify_and_refine(
    provider: MailProvider,
    *,
    email: str,
    password: str | None,
    password_from_args: str | None,
    no_verify: bool,
    account_id: str,
    label: str | None,
    provider_to_config: Callable[..., MailConfig],
    microsoft: bool = False,
    oauth2_client_id: str = "",
    oauth2_tenant: str = "",
    app_password: bool = False,
) -> tuple[int, MailConfig | None]:
    """Verify *config* by connecting, refining on failure.

    Refinement strategy (bounded):
    1. Auth-only failure → re-prompt password (max 2 attempts
       for interactively-entered passwords; 0 when ``--password``
       was supplied).
    2. Host/connection failure → manual interactive prompt for the
       failing host(s) (once).

    Returns ``(0, config)`` when verification succeeds, ``(1, None)``
    when all budgets are exhausted.  Does **not** write any files —
    the caller is responsible for reporting the result.
    """
    from robotsix_auto_mail import cli

    def _build(prov: MailProvider, pw: str | None) -> MailConfig:
        detected = provider_to_config(prov, email, password=pw or "")
        if app_password and detected.oauth2_provider:
            # Clear MSAL provider so IMAP/SMTP use plain password auth.
            detected = detected.model_copy(
                update={"oauth2_provider": "", "password": SecretStr(pw or "")}
            )
        if app_password:
            # Ensure oauth2_provider is cleared.
            detected = detected.model_copy(update={"oauth2_provider": ""})
        # Overlay explicit CLI-supplied oauth2 fields.
        if oauth2_client_id or oauth2_tenant:
            detected = detected.model_copy(
                update={
                    "oauth2_client_id": oauth2_client_id or detected.oauth2_client_id,
                    "oauth2_tenant": oauth2_tenant or detected.oauth2_tenant,
                }
            )
        return detected

    config = _build(provider, password)

    if microsoft:
        if no_verify:
            return 0, config
        # Seed the MSAL token cache via device-code consent so the post-write
        # verification can authenticate over XOAUTH2 — never a password.
        from robotsix_auto_mail.config import ConfigurationError
        from robotsix_auto_mail.oauth2 import device_code_login

        sys.stderr.write("\nRunning Microsoft device-code login…\n")
        try:
            device_code_login(config)
        except ConfigurationError as exc:
            sys.stderr.write(f"Error: {exc}\n")
            return 1, None
        except Exception as exc:  # device-flow error / user abort
            sys.stderr.write(f"Error: device-code login failed: {exc}\n")
            return 1, None
    else:
        if not config.password.get_secret_value():
            sys.stderr.write(
                "No password provided — add it to the config file "
                "(or set it via the Configure panel), then run `probe` to verify.\n"
            )
            return 0, config
        if no_verify:
            return 0, config

    # -- verify + refine loop --
    #   connection/TLS failure → manual host prompt;  auth failure →
    #   re-prompt the password.
    # only re-prompt the password when it was entered interactively; Microsoft
    # accounts never use a password, so re-prompting is always disabled.
    pw_budget = 0 if microsoft else (2 if password_from_args is None else 0)
    manual_used = False

    while True:
        sys.stderr.write("\nVerifying connection (IMAP + SMTP)…\n")
        result = cli._verify_config(config)
        if result.ok:
            sys.stderr.write("Verification succeeded — settings work.\n")
            return 0, config
        _report_verify_result(result)

        if microsoft and result.only_auth_problem:
            sys.stderr.write(
                "Microsoft XOAUTH2 authentication failed. Run "
                f"`robotsix-auto-mail auth login --account {account_id}` to "
                "(re)consent.\nIf your organisation restricts IMAP/SMTP OAuth, "
                "an Azure AD admin may need to grant the IMAP.AccessAsUser.All "
                "and SMTP.Send permissions.\n"
            )
            break

        if result.only_auth_problem and pw_budget > 0:
            pw_budget -= 1
            outcome = _refine_password(_build, provider)
            if outcome.config is None:
                break
            config = outcome.config
            continue

        if result.host_problem and not manual_used:
            manual_used = True
            outcome = _refine_manual(config, result)
            if outcome.config is None:
                break
            config = outcome.config
            continue

        break

    sys.stderr.write(
        "\nVerification FAILED — could not confirm the settings. "
        "Check the host/port/TLS values and re-run `detect`.\n"
    )
    return 1, config
