"""Proactive account health checks (IMAP + SMTP auth probes).

Provides ``probe_account()`` — a shared function that tests both IMAP
and SMTP connectivity / authentication — and ``utcnow()``, a small
UTC-timestamp helper used by callers.
"""

from __future__ import annotations

import datetime
from typing import Literal

from robotsix_auto_mail.config import MailConfig
from robotsix_auto_mail.imap import ImapClient, ImapError
from robotsix_auto_mail.smtp import SmtpClient, SmtpError


def utcnow() -> str:
    """Return the current UTC time as an ISO 8601 string (no microseconds)."""
    return (
        datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    )


def probe_account(
    config: MailConfig,
) -> tuple[Literal["ok", "failed"], str | None]:
    """Try IMAP then SMTP auth; return ``("ok", None)`` or ``("failed", "<error>")``.

    Both protocols are attempted even if IMAP fails, so the error string
    can read ``"IMAP: X; SMTP: Y"`` when both are down.  Returns
    ``("failed", ...)`` as soon as either fails.
    """
    errors: list[str] = []

    # -- IMAP ---------------------------------------------------------------
    try:
        with ImapClient(config):
            pass
    except ImapError as exc:
        errors.append(f"IMAP: {exc}")

    # -- SMTP ---------------------------------------------------------------
    try:
        with SmtpClient(config):
            pass
    except SmtpError as exc:
        errors.append(f"SMTP: {exc}")

    if errors:
        return ("failed", "; ".join(errors))
    return ("ok", None)
