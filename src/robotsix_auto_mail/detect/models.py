"""Data models for email provider detection.

Holds the internal dataclasses that represent detected mail provider
parameters and the known-provider registry entries.
"""

from __future__ import annotations

import dataclasses

from robotsix_auto_mail.config import (
    DEFAULT_IMAP_TLS_MODE,
    DEFAULT_SMTP_TLS_MODE,
)

# ---------------------------------------------------------------------------
# Internal dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MailProvider:
    """Lightweight, serialisable struct for detected mail parameters."""

    imap_host: str
    smtp_host: str
    imap_port: int = 993
    imap_tls_mode: str = DEFAULT_IMAP_TLS_MODE
    smtp_port: int = 587
    smtp_tls_mode: str = DEFAULT_SMTP_TLS_MODE


@dataclasses.dataclass(frozen=True)
class ProviderEntry:
    """Single source of truth for a known email provider."""

    label: str
    imap_host: str
    smtp_host: str
    imap_port: int = 993
    imap_tls_mode: str = DEFAULT_IMAP_TLS_MODE
    smtp_port: int = 587
    smtp_tls_mode: str = DEFAULT_SMTP_TLS_MODE
    mx_needles: tuple[str, ...] = ()
