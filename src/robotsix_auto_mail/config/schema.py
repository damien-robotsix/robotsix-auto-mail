"""Configuration schema: errors, validation constants and the field spec.

This module is the dependency leaf of the ``config`` package — it imports
nothing from its sibling submodules.  It defines the configuration error
type, the validation constants and defaults, the boolean parser, the
per-field spec table (``_FIELD_SPECS``) that is the single source of truth
for how every ``MailConfig`` field is read from the environment and from
YAML, plus the generic dict-extraction helpers used by the model loaders.
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
    message names *path* and both remediation commands: ``migrate-config``
    (convert the existing file) and ``detect`` (regenerate from scratch).
    """
    return (
        f"Config {path} uses the single-account config shape, which is no "
        "longer supported. Run `robotsix-auto-mail migrate-config` to convert "
        "this file to the multi-account `accounts:` shape, or "
        "`robotsix-auto-mail detect` to regenerate it."
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

_VALID_CALENDAR_TRANSPORTS = frozenset({"in-process", "brokered"})

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR"})
_VALID_LOG_FORMATS = frozenset({"json", "console"})

# The validation sets above are imported by model.py and detect/models.py
# for field-level validation.  This module-level reference ensures they are
# treated as "used" by module-local static analysis (CodeQL).
_VALIDATION_SETS = (
    _VALID_TLS_MODES,
    _VALID_CALENDAR_TRANSPORTS,
    _VALID_LOG_LEVELS,
    _VALID_LOG_FORMATS,
)

# Default TLS modes for IMAP and SMTP connections.
DEFAULT_IMAP_TLS_MODE = "direct-tls"
DEFAULT_SMTP_TLS_MODE = "starttls"

# Default location for the SQLite store: a ``.data/`` directory next to the
# current working directory (git-ignored), keeping the repo root clean.
DEFAULT_DB_PATH = ".data/mail.db"

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
    """How to read one ``MailConfig`` field from the env and the YAML file.

    ``env_key`` is the environment variable name; ``yaml_path`` is a dotted
    ``section.key`` pair (exactly two segments).  ``kind`` selects the
    parser / validator: ``"str"``, ``"int"`` or ``"tls_mode"``.  ``default``
    is the value used when the source is absent and the field is not
    required for that source (or :data:`_REQUIRED` if no real default
    exists).  ``required_in_env`` / ``required_in_yaml`` are intentionally
    independent — ``password`` is required in env but not in YAML.

    ``global_field`` marks a field that is application-wide (llm, langfuse);
    in the multi-account loader these fields are read from bare (non-namespaced)
    env vars, not from ``MAIL_ACCOUNTS_<n>_*``.
    """

    field_name: str
    env_key: str
    yaml_path: str
    kind: str
    default: Any
    required_in_env: bool
    required_in_yaml: bool
    global_field: bool = False


_FIELD_SPECS: Final[tuple[_FieldSpec, ...]] = (
    _FieldSpec(
        "imap_host", "MAIL_IMAP_HOST", "imap.host", "str", _REQUIRED, True, True
    ),
    _FieldSpec("imap_port", "MAIL_IMAP_PORT", "imap.port", "int", 993, False, False),
    _FieldSpec(
        "imap_tls_mode",
        "MAIL_IMAP_TLS_MODE",
        "imap.tls_mode",
        "tls_mode",
        DEFAULT_IMAP_TLS_MODE,
        False,
        False,
    ),
    _FieldSpec(
        "imap_folder", "MAIL_IMAP_FOLDER", "imap.folder", "str", "INBOX", False, False
    ),
    _FieldSpec(
        "smtp_host", "MAIL_SMTP_HOST", "smtp.host", "str", _REQUIRED, True, True
    ),
    _FieldSpec("smtp_port", "MAIL_SMTP_PORT", "smtp.port", "int", 587, False, False),
    _FieldSpec(
        "smtp_tls_mode",
        "MAIL_SMTP_TLS_MODE",
        "smtp.tls_mode",
        "tls_mode",
        DEFAULT_SMTP_TLS_MODE,
        False,
        False,
    ),
    _FieldSpec(
        "username", "MAIL_USERNAME", "auth.username", "str", _REQUIRED, True, True
    ),
    # password: required in env, but optional in YAML (env can supply it).
    _FieldSpec(
        "password", "MAIL_PASSWORD", "auth.password", "str", _REQUIRED, True, False
    ),
    _FieldSpec(
        "db_path", "MAIL_DB_PATH", "store.path", "str", DEFAULT_DB_PATH, False, False
    ),
    _FieldSpec(
        "llm_api_key",
        "LLM_API_KEY",
        "llm.api_key",
        "str",
        "",
        False,
        False,
        global_field=True,
    ),
    _FieldSpec(
        "llm_provider_model",
        "LLM_PROVIDER_MODEL",
        "llm.provider_model",
        "str",
        "",
        False,
        False,
        global_field=True,
    ),
    _FieldSpec(
        "ingest_interval_minutes",
        "MAIL_INGEST_INTERVAL",
        "ingest.interval_minutes",
        "int",
        DEFAULT_INGEST_INTERVAL_MINUTES,
        False,
        False,
    ),
    _FieldSpec(
        "archive_root",
        "MAIL_ARCHIVE_ROOT",
        "archive.root",
        "str",
        DEFAULT_ARCHIVE_ROOT,
        False,
        False,
    ),
    _FieldSpec(
        "archive_namespace",
        "MAIL_ARCHIVE_NAMESPACE",
        "archive.namespace",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "archive_enabled",
        "MAIL_ARCHIVE_ENABLED",
        "archive.enabled",
        "bool",
        True,
        False,
        False,
    ),
    _FieldSpec(
        "triage_on_ingest",
        "MAIL_TRIAGE_ON_INGEST",
        "triage.on_ingest",
        "bool",
        True,
        False,
        False,
    ),
    _FieldSpec(
        "calendar_enabled",
        "MAIL_CALENDAR_ENABLED",
        "calendar.enabled",
        "bool",
        True,
        False,
        False,
    ),
    # OAuth2 / XOAUTH2 — optional; when oauth2_token is set, SASL XOAUTH2
    # is used instead of password-based login().
    _FieldSpec(
        "oauth2_token",
        "MAIL_OAUTH2_TOKEN",
        "auth.oauth2_token",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "oauth2_client_id",
        "MAIL_OAUTH2_CLIENT_ID",
        "auth.oauth2_client_id",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "oauth2_client_secret",
        "MAIL_OAUTH2_CLIENT_SECRET",
        "auth.oauth2_client_secret",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "oauth2_provider",
        "MAIL_OAUTH2_PROVIDER",
        "auth.oauth2_provider",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "oauth2_tenant",
        "MAIL_OAUTH2_TENANT",
        "auth.oauth2_tenant",
        "str",
        "organizations",
        False,
        False,
    ),
    _FieldSpec(
        "langfuse_public_key",
        "LANGFUSE_PUBLIC_KEY",
        "langfuse.public_key",
        "str",
        "",
        False,
        False,
        global_field=True,
    ),
    _FieldSpec(
        "langfuse_secret_key",
        "LANGFUSE_SECRET_KEY",
        "langfuse.secret_key",
        "str",
        "",
        False,
        False,
        global_field=True,
    ),
    _FieldSpec(
        "langfuse_base_url",
        "LANGFUSE_BASE_URL",
        "langfuse.base_url",
        "str",
        "",
        False,
        False,
        global_field=True,
    ),
    _FieldSpec(
        "log_level",
        "LOG_LEVEL",
        "logging.level",
        "log_level",
        "INFO",
        False,
        False,
        global_field=True,
    ),
    _FieldSpec(
        "log_format",
        "LOG_FORMAT",
        "logging.format",
        "log_format",
        "console",
        False,
        False,
        global_field=True,
    ),
    _FieldSpec(
        "log_file_dir",
        "LOG_FILE_DIR",
        "logging.file_dir",
        "str",
        ".mail_log",
        False,
        False,
        global_field=True,
    ),
    _FieldSpec(
        "calendar_transport",
        "CALENDAR_TRANSPORT",
        "calendar.transport",
        "calendar_transport",
        "in-process",
        False,
        False,
    ),
    _FieldSpec(
        "calendar_broker_host",
        "CALENDAR_BROKER_HOST",
        "calendar.broker_host",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "calendar_broker_port",
        "CALENDAR_BROKER_PORT",
        "calendar.broker_port",
        "int",
        443,
        False,
        False,
    ),
    _FieldSpec(
        "calendar_broker_tls_ca",
        "CALENDAR_BROKER_TLS_CA",
        "calendar.broker_tls_ca",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "calendar_broker_client_cert",
        "CALENDAR_BROKER_CLIENT_CERT",
        "calendar.broker_client_cert",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "calendar_broker_client_key",
        "CALENDAR_BROKER_CLIENT_KEY",
        "calendar.broker_client_key",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "calendar_broker_token",
        "CALENDAR_BROKER_TOKEN",
        "calendar.broker_token",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "component_agent_enabled",
        "COMPONENT_AGENT_ENABLED",
        "component_agent.enabled",
        "bool",
        False,
        False,
        False,
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


def _get_bool(section: dict[str, object], key: str, default: bool) -> bool:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigurationError(
            f"Config key {key!r} must be a boolean, got {type(value).__name__}"
        )
    return value
