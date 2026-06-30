"""Data models for email provider detection.

Holds the exception type, pydantic LLM-output contract, and internal
dataclasses that represent detected/imputed mail provider parameters.
"""

from __future__ import annotations

import dataclasses

import pydantic

from robotsix_auto_mail.config import (
    _VALID_TLS_MODES,
    DEFAULT_IMAP_TLS_MODE,
    DEFAULT_SMTP_TLS_MODE,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DetectionError(Exception):
    """Raised when provider detection fails for any reason."""


# ---------------------------------------------------------------------------
# Pydantic model — structured LLM output contract
# ---------------------------------------------------------------------------


class DetectedProvider(pydantic.BaseModel):
    """Structured output the LLM must return — validated by pydantic."""

    imap_host: str = pydantic.Field(..., min_length=1)
    imap_port: int = pydantic.Field(default=993, ge=1, le=65535)
    imap_tls_mode: str = pydantic.Field(default=DEFAULT_IMAP_TLS_MODE)
    smtp_host: str = pydantic.Field(..., min_length=1)
    smtp_port: int = pydantic.Field(default=587, ge=1, le=65535)
    smtp_tls_mode: str = pydantic.Field(default=DEFAULT_SMTP_TLS_MODE)

    @pydantic.field_validator("imap_tls_mode", "smtp_tls_mode")
    @classmethod
    def _validate_tls_mode(cls, v: str) -> str:
        if v not in _VALID_TLS_MODES:
            raise ValueError(
                f"TLS mode must be one of {sorted(_VALID_TLS_MODES)!r}; got {v!r}"
            )
        return v


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
    domain_patterns: tuple[str, ...] = ()
    in_prompt_table: bool = True
