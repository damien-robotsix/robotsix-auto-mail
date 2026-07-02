"""Configuration schema: errors, validation constants.

This module is the dependency leaf of the ``config`` package — it imports
nothing from its sibling submodules.  It defines the configuration error
type, the validation constants and defaults, and the mono-shape error message.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, Literal

from robotsix_auto_mail._constants import _ARCHIVE_ROOT

# ---------------------------------------------------------------------------
# Removal of the single-account ("mono") YAML file config shape
# ---------------------------------------------------------------------------


def _mono_shape_error(path: Path) -> str:
    """Return the actionable error for a removed single-account YAML file.

    The single-account ("mono") YAML file shape is no longer supported. The
    message names *path* and points at ``detect`` to (re)generate a
    multi-account ``accounts:`` config.
    """
    return (
        f"Config {path} does not use the multi-account `accounts:` shape "
        "(the single-account shape is no longer supported). Add an "
        "`accounts:` list, or run `robotsix-auto-mail detect` to generate one."
    )


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

_VALID_TLS_MODES = frozenset({"starttls", "direct-tls", "none"})

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
_VALID_LOG_FORMATS = frozenset({"json", "console"})

# The validation sets above are imported by model.py and detect/models.py
# for field-level validation.  This module-level reference ensures they are
# treated as "used" by module-local static analysis (CodeQL).
_VALIDATION_SETS = (
    _VALID_TLS_MODES,
    _VALID_LOG_LEVELS,
    _VALID_LOG_FORMATS,
)

# Default TLS modes for IMAP and SMTP connections.
DEFAULT_IMAP_TLS_MODE: Literal["starttls", "direct-tls", "none"] = "direct-tls"
DEFAULT_SMTP_TLS_MODE: Literal["starttls", "direct-tls", "none"] = "starttls"

# Default interval (minutes) between automatic ingest cycles in watch mode.
DEFAULT_INGEST_INTERVAL_MINUTES = 15

# Default root folder under which the self-managed archive structure lives.
DEFAULT_ARCHIVE_ROOT: Final = _ARCHIVE_ROOT
