"""Configuration schema: errors, validation constants and defaults.

This module is the dependency leaf of the ``config`` package — it imports
nothing from its sibling submodules.  It defines the configuration error
type, the validation constants and defaults.  The per-field spec table
(``_FIELD_SPECS``) and YAML-parsing helpers have been removed — pydantic
model declarations now serve as the single source of truth.
"""

from __future__ import annotations

from typing import Final

from robotsix_auto_mail.core._constants import _ARCHIVE_ROOT

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ConfigurationError(Exception):
    """Raised when the mail configuration is invalid or incomplete.

    Attributes:
        message: Human-readable error description.
        missing_only: True when the *only* problem is missing required
            fields (no invalid values).  Used by ``load()`` to decide
            whether falling back to the YAML config file is appropriate.
    """

    def __init__(self, message: str, *, missing_only: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.missing_only = missing_only

    def __str__(self) -> str:
        return self.message


# ---------------------------------------------------------------------------
# Valid TLS modes
# ---------------------------------------------------------------------------

_VALID_TLS_MODES: Final[frozenset[str]] = frozenset({"starttls", "direct-tls", "none"})

_VALID_LOG_LEVELS: Final[frozenset[str]] = frozenset(
    {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
)
_VALID_LOG_FORMATS: Final[frozenset[str]] = frozenset({"json", "console"})

# The validation sets above are imported by model.py and detect/models.py
# for field-level validation.  This module-level reference ensures they are
# treated as "used" by module-local static analysis (CodeQL).
_VALIDATION_SETS = (
    _VALID_TLS_MODES,
    _VALID_LOG_LEVELS,
    _VALID_LOG_FORMATS,
)
_ = _VALIDATION_SETS  # suppress CodeQL py/unused-global-variable

# Default TLS modes for IMAP and SMTP connections.
DEFAULT_IMAP_TLS_MODE: Final[str] = "direct-tls"
DEFAULT_SMTP_TLS_MODE: Final[str] = "starttls"

# Default interval (minutes) between automatic ingest cycles in watch mode.
DEFAULT_INGEST_INTERVAL_MINUTES: Final[int] = 15

# Default root folder under which the self-managed archive structure lives.
DEFAULT_ARCHIVE_ROOT: Final[str] = _ARCHIVE_ROOT
