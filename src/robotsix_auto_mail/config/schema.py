"""Configuration schema: errors, validation constants and the field spec.

This module is the dependency leaf of the ``config`` package — it imports
nothing from its sibling submodules.  It defines the configuration error
type, the validation constants and defaults, the boolean parser, the
per-field spec table (``_FIELD_SPECS``) that is the single source of truth
for how every ``MailConfig`` field is read from the YAML config file, plus
the generic dict-extraction helpers used by the model loaders.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final, NamedTuple

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
DEFAULT_IMAP_TLS_MODE = "direct-tls"
DEFAULT_SMTP_TLS_MODE = "starttls"

# Default interval (minutes) between automatic ingest cycles in watch mode.
DEFAULT_INGEST_INTERVAL_MINUTES = 15

# Default root folder under which the self-managed archive structure lives.
DEFAULT_ARCHIVE_ROOT: Final = _ARCHIVE_ROOT


_BOOL_TRUE = frozenset({"1", "true", "yes", "on"})
_BOOL_FALSE = frozenset({"0", "false", "no", "off"})


def _parse_bool(label: str, raw: str) -> bool:
    lowered = raw.lower()
    if lowered in _BOOL_TRUE:
        return True
    if lowered in _BOOL_FALSE:
        return False
    raise ConfigurationError(f"{label} must be a boolean, got {raw!r}")


# ---------------------------------------------------------------------------
# Per-field spec table (single source of truth)
# ---------------------------------------------------------------------------

# Sentinel marking a field with no real default value: callers (env loader,
# YAML loader) decide what to use as a fallback when the value is absent.
_REQUIRED: Final[object] = object()


class _FieldSpec(NamedTuple):
    """How to read one ``MailConfig`` field from the YAML config file.

    ``yaml_path`` is a dotted ``section.key`` pair (exactly two segments).
    ``kind`` selects the parser / validator: ``"str"``, ``"int"``,
    ``"bool"``, ``"tls_mode"``, ``"log_level"`` or ``"log_format"``.
    ``default`` is the value used when the key is absent (or
    :data:`_REQUIRED` if no real default exists).  ``required_in_yaml``
    marks a field that must be present in the config file.

    The application-wide sections (``llm``, ``langfuse``, ``logging``) live
    at the top level of the YAML file and are applied to every account by
    the accounts loader.
    """

    field_name: str
    yaml_path: str
    kind: str
    default: Any
    required_in_yaml: bool


_FIELD_SPECS: Final[tuple[_FieldSpec, ...]] = (
    _FieldSpec("imap_host", "imap.host", "str", _REQUIRED, True),
    _FieldSpec("imap_port", "imap.port", "int", 993, False),
    _FieldSpec(
        "imap_tls_mode", "imap.tls_mode", "tls_mode", DEFAULT_IMAP_TLS_MODE, False
    ),
    _FieldSpec("imap_folder", "imap.folder", "str", "INBOX", False),
    _FieldSpec("smtp_host", "smtp.host", "str", _REQUIRED, True),
    _FieldSpec("smtp_port", "smtp.port", "int", 587, False),
    _FieldSpec(
        "smtp_tls_mode", "smtp.tls_mode", "tls_mode", DEFAULT_SMTP_TLS_MODE, False
    ),
    _FieldSpec("username", "auth.username", "str", _REQUIRED, True),
    # password: optional in YAML (may be supplied, or unused with OAuth2).
    _FieldSpec("password", "auth.password", "str", _REQUIRED, False),
    # db_path: empty default; the accounts loader derives ``.data/<id>/mail.db``
    # per account when ``store.path`` is absent.
    _FieldSpec("db_path", "store.path", "str", "", False),
    _FieldSpec("llm_api_key", "llm.api_key", "str", "", False),
    _FieldSpec("llm_provider_model", "llm.provider_model", "str", "", False),
    _FieldSpec(
        "ingest_interval_minutes",
        "ingest.interval_minutes",
        "int",
        DEFAULT_INGEST_INTERVAL_MINUTES,
        False,
    ),
    _FieldSpec("archive_root", "archive.root", "str", DEFAULT_ARCHIVE_ROOT, False),
    _FieldSpec("archive_enabled", "archive.enabled", "bool", True, False),
    _FieldSpec("triage_on_ingest", "triage.on_ingest", "bool", True, False),
    # Path to the human-readable triage rules file that the flash LLM
    # maintains from user actions.  Empty means "derive from db_path"
    # (``<db-dir>/triage_rules.md``); per-account like db_path.
    _FieldSpec("triage_rules_path", "triage.rules_path", "str", "", False),
    # OAuth2 / XOAUTH2 — optional; when oauth2_token is set, SASL XOAUTH2
    # is used instead of password-based login().
    _FieldSpec("oauth2_token", "auth.oauth2_token", "str", "", False),
    _FieldSpec("oauth2_client_id", "auth.oauth2_client_id", "str", "", False),
    _FieldSpec("oauth2_client_secret", "auth.oauth2_client_secret", "str", "", False),
    _FieldSpec("oauth2_provider", "auth.oauth2_provider", "str", "", False),
    _FieldSpec("oauth2_tenant", "auth.oauth2_tenant", "str", "organizations", False),
    _FieldSpec(
        "langfuse_public_key",
        "langfuse.public_key",
        "str",
        "",
        False,
    ),
    _FieldSpec(
        "langfuse_secret_key",
        "langfuse.secret_key",
        "str",
        "",
        False,
    ),
    _FieldSpec("langfuse_base_url", "langfuse.base_url", "str", "", False),
    _FieldSpec("log_level", "logging.level", "log_level", "INFO", False),
    _FieldSpec(
        "log_format",
        "logging.format",
        "log_format",
        "console",
        False,
    ),
    _FieldSpec(
        "log_file_dir",
        "logging.file_dir",
        "str",
        ".mail_log",
        False,
    ),
    _FieldSpec(
        "component_agent_enabled", "component_agent.enabled", "bool", False, False
    ),
)

# Each yaml_path must be exactly ``section.key`` — the YAML loader splits
# on the single dot.  Validated once at import time so a typo here fails
# immediately rather than at first use.
for _s in _FIELD_SPECS:
    assert _s.yaml_path.count(".") == 1, (  # noqa: S101  # nosec B101
        f"_FieldSpec.yaml_path must have exactly one dot, got {_s.yaml_path!r}"
    )

# Validate that the validation sets (imported by model.py) are non-empty.
assert all(_VALIDATION_SETS), "validation sets must be non-empty"  # noqa: S101  # nosec B101


# ---------------------------------------------------------------------------
# Internal helpers for YAML parsing
# ---------------------------------------------------------------------------


def _get_table(data: dict[str, object], key: str) -> dict[str, object] | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ConfigurationError(
            f"Config key {key!r} must be a table/mapping, got {type(value).__name__}"
        )
    return value


def _get_str(section: dict[str, object], key: str, default: str) -> str:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigurationError(
            f"Config key {key!r} must be a string, got {type(value).__name__}"
        )
    return value


def _get_int(section: dict[str, object], key: str, default: int, path: Path) -> int:
    value = section.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        raise ConfigurationError(
            f"Config key {key!r} must be an integer, got bool ({value!r})"
        )
    if not isinstance(value, int):
        raise ConfigurationError(
            f"Config key {key!r} must be an integer, got {type(value).__name__}"
        )
    return value
