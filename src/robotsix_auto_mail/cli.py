"""CLI for robotsix-auto-mail.

Entry point: ``main()``, exposed via console_scripts in pyproject.toml.
"""

from __future__ import annotations

import argparse
import sys
from typing import TextIO

from robotsix_auto_mail import __version__
from robotsix_auto_mail.config import MailConfig, load
from robotsix_auto_mail.imap import ImapClient, ImapError
from robotsix_auto_mail.smtp_client import SmtpClient, SmtpError


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
    sub.add_parser("probe", help="Probe IMAP and SMTP servers for diagnostics")

    return parser


def _print_header(
    file: TextIO, title: str, width: int = 60, char: str = "-"
) -> None:
    file.write(f"\n{title}\n{char * width}\n")


def _cmd_probe(config: MailConfig) -> int:
    """Run the probe subcommand: connect to IMAP + SMTP and print metadata.

    Returns 0 when both succeed, 1 when either fails.
    """
    failures = 0

    # -- IMAP ---------------------------------------------------------------
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
        failures += 1

    # -- SMTP ---------------------------------------------------------------
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
        failures += 1

    return 0 if failures == 0 else 1


def main(argv: list[str] | None = None) -> int:
    """Parse args and dispatch to the appropriate subcommand handler.

    Returns 0 on success, 1 on failure.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "probe":
        try:
            config = load()
        except Exception as exc:
            sys.stderr.write(f"Error loading configuration: {exc}\n")
            return 1
        return _cmd_probe(config)

    # No command given — print help and exit 1.
    parser.print_help(sys.stderr)
    return 1