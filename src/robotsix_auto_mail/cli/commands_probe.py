"""Probe command handler — extracted from commands.py."""

from __future__ import annotations

import sys

from robotsix_auto_mail.cli.commands import _print_header
from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.health import probe_account
from robotsix_auto_mail.imap import ImapClient, ImapError
from robotsix_auto_mail.smtp import SmtpClient, SmtpError


def _cmd_probe(config: MailConfig) -> int:
    """Run the probe subcommand: connect to IMAP + SMTP and print metadata.

    Uses the shared ``probe_account()`` to obtain a structured pass/fail
    verdict and then separately renders human-readable diagnostic output
    (greeting, capabilities, folders, EHLO).  The CLI behaviour is unchanged.

    Returns 0 when both succeed, 1 when either fails.
    """
    # Structured pass/fail from the shared health probe (the per-protocol
    # diagnostics below re-probe and surface their own errors).
    status, _ = probe_account(config)

    # -- IMAP diagnostic output -------------------------------------------
    _print_header(sys.stdout, "IMAP Probe")

    try:
        with ImapClient(config) as imap:
            greeting = imap.server_greeting
            if greeting is not None:
                sys.stdout.write(
                    f"Greeting: {greeting.decode('utf-8', errors='replace')}\n"
                )
            else:
                sys.stdout.write("Greeting: (none)\n")

            sys.stdout.write("Capabilities:\n")
            for cap in imap.capabilities:
                sys.stdout.write(f"  - {cap}\n")

            # Folders
            sys.stdout.write("\nFolders:\n")
            folders = imap.list_folders()
            if folders:
                for fi in folders:
                    attrs = " ".join(fi.attributes) if fi.attributes else "(none)"
                    delim = fi.delimiter if fi.delimiter else "(none)"
                    sys.stdout.write(f"  {fi.name}\n")
                    sys.stdout.write(f"    attributes: {attrs}\n")
                    sys.stdout.write(f"    delimiter:  {delim}\n")
            else:
                sys.stdout.write("  (no folders returned)\n")
    except ImapError as exc:
        sys.stderr.write(f"Error: {exc}\n")

    # -- SMTP diagnostic output -------------------------------------------
    _print_header(sys.stdout, "SMTP Probe")

    try:
        with SmtpClient(config) as smtp:
            ehlo = smtp.ehlo_response
            if ehlo is not None:
                sys.stdout.write(
                    f"EHLO response: {ehlo.decode('utf-8', errors='replace')}\n"
                )
            else:
                sys.stdout.write("EHLO response: (none)\n")

            sys.stdout.write("\nESMTP features:\n")
            features = smtp.esmtp_features
            if features:
                for key, value in sorted(features.items()):
                    sys.stdout.write(f"  {key}: {value}\n")
            else:
                sys.stdout.write("  (no features)\n")

            # Deliberately: no send() call.  This is diagnostic-only.
    except SmtpError as exc:
        sys.stderr.write(f"Error: {exc}\n")

    return 0 if status == "ok" else 1
