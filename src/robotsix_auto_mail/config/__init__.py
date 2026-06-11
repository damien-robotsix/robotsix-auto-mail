"""Mail configuration subsystem.

Provides ``MailConfig``, a frozen dataclass that holds IMAP and SMTP
connection parameters, with two loaders: ``from_env()`` (environment
variables) and ``from_yaml()`` (a YAML file).

Configuration resolves through a single, predictable cascade — see
``load()``: code defaults → YAML file → environment variables (which
win field-by-field).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Final, NamedTuple

from robotsix_yaml_config import (  # type: ignore[import-untyped]
    YamlConfigError,
    read_yaml_file,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Deprecation of the single-account ("mono") config shape
# ---------------------------------------------------------------------------


def _warn_deprecated_mono_shape(source: str) -> None:
    """Emit a deprecation signal for the legacy single-account config shape.

    *source* names where the deprecated shape was found (a file path or the
    environment scheme).  The same actionable message is raised as a
    :class:`DeprecationWarning` *and* logged at ``warning`` level so it
    surfaces both in test assertions and at CLI startup (``main()`` loads the
    config best-effort during tracing init).

    The single-account shape is retained for now; removal happens in a later
    ticket once operator configs have been migrated via ``migrate-config``.
    """
    message = (
        f"{source} uses the deprecated single-account config shape; run "
        "`robotsix-auto-mail migrate-config` to convert it to the "
        "multi-account `accounts:` shape. The single-account shape will be "
        "removed in a future release."
    )
    warnings.warn(message, DeprecationWarning, stacklevel=3)
    logger.warning(message)


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

# Default TLS modes for IMAP and SMTP connections.
DEFAULT_IMAP_TLS_MODE = "direct-tls"
DEFAULT_SMTP_TLS_MODE = "starttls"

# Default location for the SQLite store: a ``.data/`` directory next to the
# current working directory (git-ignored), keeping the repo root clean.
DEFAULT_DB_PATH = ".data/mail.db"

# Default YAML config file path (used by ``load()`` and ``load_llm()``).
DEFAULT_CONFIG_PATH = "config/mail.local.yaml"

# Default interval (minutes) between automatic ingest cycles in watch mode.
DEFAULT_INGEST_INTERVAL_MINUTES = 15

# Default root folder under which the self-managed archive structure lives.
DEFAULT_ARCHIVE_ROOT = "robotsix-mail-archive"


def _check_tls_mode(label: str, value: str) -> None:
    if value not in _VALID_TLS_MODES:
        raise ConfigurationError(
            f"{label} must be one of {sorted(_VALID_TLS_MODES)!r}, got {value!r}"
        )


def _parse_int(label: str, raw: str) -> int:
    try:
        return int(raw)
    except (ValueError, TypeError):
        raise ConfigurationError(f"{label} must be an integer, got {raw!r}") from None


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
    """

    field_name: str
    env_key: str
    yaml_path: str
    kind: str
    default: Any
    required_in_env: bool
    required_in_yaml: bool


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
    _FieldSpec("llm_api_key", "LLM_API_KEY", "llm.api_key", "str", "", False, False),
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
        "langfuse_public_key",
        "MAIL_LANGFUSE_PUBLIC_KEY",
        "langfuse.public_key",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "langfuse_secret_key",
        "MAIL_LANGFUSE_SECRET_KEY",
        "langfuse.secret_key",
        "str",
        "",
        False,
        False,
    ),
    _FieldSpec(
        "langfuse_base_url",
        "MAIL_LANGFUSE_BASE_URL",
        "langfuse.base_url",
        "str",
        "",
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


# ---------------------------------------------------------------------------
# MailConfig
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class MailConfig:
    """Immutable application settings: mail server connection parameters
    plus optional LLM credentials used by ``detect`` (and future mail
    processing).

    Credentials are stored in memory as plain ``str`` values but the
    ``password`` and ``llm_api_key`` fields are masked in ``repr`` / ``str``.
    """

    imap_host: str
    smtp_host: str
    username: str
    password: str

    imap_port: int = 993
    imap_tls_mode: str = DEFAULT_IMAP_TLS_MODE
    smtp_port: int = 587
    smtp_tls_mode: str = DEFAULT_SMTP_TLS_MODE

    db_path: str = DEFAULT_DB_PATH
    imap_folder: str = "INBOX"

    # LLM provider settings — optional; only needed for the `detect`
    # subcommand and future LLM-assisted mail processing.
    llm_api_key: str = ""

    # Minutes between automatic ingest cycles (`ingest --watch`).
    ingest_interval_minutes: int = DEFAULT_INGEST_INTERVAL_MINUTES

    # Self-managed archive folder structure.
    archive_root: str = DEFAULT_ARCHIVE_ROOT
    archive_namespace: str = ""
    archive_enabled: bool = True

    # Run the inbox triage agent automatically at the end of each ingest.
    triage_on_ingest: bool = True

    # OAuth2 / XOAUTH2 credentials (Gmail, Microsoft 365, etc.).
    # Optional; when ``oauth2_token`` is set, SASL XOAUTH2 is used
    # instead of password-based ``login()``.
    oauth2_token: str = ""
    oauth2_client_id: str = ""
    oauth2_client_secret: str = ""

    # Langfuse observability — optional; when public_key/secret_key are set,
    # every LLM agent run is traced to the configured Langfuse project.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""

    # -- masking -----------------------------------------------------------

    _SECRET_FIELDS = (
        "password",
        "llm_api_key",
        "oauth2_token",
        "oauth2_client_secret",
        "langfuse_secret_key",
    )

    def __repr__(self) -> str:
        cls = type(self).__name__
        fields = dataclasses.fields(self)
        parts = []
        for f in fields:
            val = getattr(self, f.name)
            if f.name in self._SECRET_FIELDS:
                parts.append(f"{f.name}=<redacted>")
            else:
                parts.append(f"{f.name}={val!r}")
        return f"{cls}({', '.join(parts)})"

    def __str__(self) -> str:
        return self.__repr__()

    # -- loaders -----------------------------------------------------------

    @classmethod
    def from_env(cls) -> MailConfig:
        """Build a ``MailConfig`` from environment variables.

        Required env vars:
          ``MAIL_IMAP_HOST``, ``MAIL_SMTP_HOST``, ``MAIL_USERNAME``,
          ``MAIL_PASSWORD``

        Optional env vars (with defaults):
          ``MAIL_IMAP_PORT`` (993), ``MAIL_IMAP_TLS_MODE`` (direct-tls),
          ``MAIL_SMTP_PORT`` (587), ``MAIL_SMTP_TLS_MODE`` (starttls)

        Returns:
            A fully-populated ``MailConfig``.

        Raises:
            ConfigurationError: If any required variable is missing or
                any value is invalid.
        """
        return _build_config_from_env(
            lambda spec: os.environ.get(spec.env_key, ""),
            lambda spec: spec.env_key,
        )

    @classmethod
    def _parse_config_dict(
        cls, data: dict[str, object], path: Path, *, validate: bool = True
    ) -> MailConfig:
        errors: list[str] = []
        kwargs: dict[str, Any] = {}
        # Memoise top-level section lookups so we don't re-validate
        # the same mapping for every field that lives under it.
        sections: dict[str, dict[str, object]] = {}

        for spec in _FIELD_SPECS:
            section_name, key_name = spec.yaml_path.split(".", 1)
            if section_name not in sections:
                sections[section_name] = _get_table(data, section_name) or {}
            section = sections[section_name]

            if spec.kind == "int":
                kwargs[spec.field_name] = _get_int(
                    section, key_name, spec.default, path
                )
            elif spec.kind == "bool":
                kwargs[spec.field_name] = _get_bool(section, key_name, spec.default)
            elif spec.kind == "tls_mode":
                value = _get_str(section, key_name, spec.default)
                if value not in _VALID_TLS_MODES:
                    errors.append(
                        f"{spec.yaml_path} must be one of "
                        f"{sorted(_VALID_TLS_MODES)!r}, got {value!r}"
                    )
                kwargs[spec.field_name] = value
            else:  # "str"
                default_str = "" if spec.default is _REQUIRED else spec.default
                kwargs[spec.field_name] = _get_str(section, key_name, default_str)

        # -- required fields (skipped when validate=False) -----------------

        if validate:
            missing: list[str] = []
            for spec in _FIELD_SPECS:
                if spec.required_in_yaml and not kwargs[spec.field_name]:
                    missing.append(spec.yaml_path)
            if missing:
                errors.append("Missing required field(s): " + ", ".join(missing))

        if errors:
            raise ConfigurationError("\n".join(errors))

        return cls(**kwargs)

    @classmethod
    def from_yaml(cls, path: str | Path, *, validate: bool = True) -> MailConfig:
        """Build a ``MailConfig`` from a YAML file.

        The file is expected to follow this structure::

            imap:
              host: imap.example.com
              port: 993
              tls_mode: direct-tls

            smtp:
              host: smtp.example.com
              port: 587
              tls_mode: starttls

            auth:
              username: user@example.com
              password: s3cret

            llm:
              api_key: sk-or-v1-…

        All fields are optional; missing fields fall back to the same
        defaults as ``from_env()``.

        Args:
            path: Filesystem path to the YAML file.
            validate: If True (the default), raise ConfigurationError
                when required fields are empty.  Set to False to load a
                partial file that intentionally leaves required fields
                blank (e.g. round-tripping ``detect`` output in tests).

        Returns:
            A fully-populated ``MailConfig``.

        Raises:
            ConfigurationError: If the file cannot be parsed or (when
                *validate* is True) if required fields are missing.
            FileNotFoundError: If *path* does not exist.
        """
        path = Path(path)

        # ``read_yaml_file`` returns an empty dict for a missing file; we
        # still want ``from_yaml`` to surface a FileNotFoundError so callers
        # (e.g. ``load()``) can distinguish "no file" from "empty file".
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        try:
            data = read_yaml_file(path)
        except YamlConfigError as exc:
            raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc

        return cls._parse_config_dict(data, path, validate=validate)


# ---------------------------------------------------------------------------
# Self-consistency: ``_FIELD_SPECS`` must enumerate every dataclass field
# exactly once.  If they drift apart, import fails immediately — making
# "add a new field" a one-place edit.
# ---------------------------------------------------------------------------

_spec_names = {s.field_name for s in _FIELD_SPECS}
_dc_names = {f.name for f in dataclasses.fields(MailConfig)}
assert _spec_names == _dc_names, (  # noqa: S101  # nosec B101
    f"_FIELD_SPECS / MailConfig drift: "
    f"missing from specs={_dc_names - _spec_names}, "
    f"missing from dataclass={_spec_names - _dc_names}"
)


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------


def load() -> MailConfig:
    """Load the **default account's** :class:`MailConfig`.

    Delegates to :func:`load_accounts` and returns the default account's
    config.  This works against both the multi-account shape (the default,
    or first, account is used) and a deprecated single-account file/env
    (loaded via the compat path, which also emits a deprecation warning).

    Kept as a thin convenience for the best-effort Langfuse tracing init in
    ``cli.main()`` and for ``load_llm``-style callers that only need one
    representative account's settings.
    """
    return load_accounts().default.config


def _load_mono_config() -> MailConfig:
    """Resolve a single ``MailConfig`` through the cascade defaults → file → env.

    1.  Call ``MailConfig.from_env()``.  If all required fields are
        present in the environment, return immediately (env wins).
    2.  Otherwise, if *only* required fields are missing (no invalid
        values), load the YAML config file at ``MAIL_CONFIG_PATH``
        (defaulting to ``config/mail.local.yaml``).
    3.  *Re-apply* environment variables on top, so any ``MAIL_*`` var
        that IS set overrides the corresponding file value field-by-field.

    Defaults live in the ``MailConfig`` dataclass — fields absent from
    both the file and the environment fall back to those.

    If ``from_env()`` fails because of an invalid value (e.g. a
    non-integer port), the error is re-raised immediately — the user
    explicitly set an env var and a typo should not be silently
    swallowed by a file fallback.

    This is the legacy single-account resolver; :func:`load_accounts` wraps
    its result in the one-element ``"default"`` container for the deprecated
    mono shape.
    """
    # — attempt from_env alone —
    try:
        return MailConfig.from_env()
    except ConfigurationError as exc:
        if not exc.missing_only:
            raise

    # — load the YAML config file —
    config_path = Path(os.environ.get("MAIL_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    try:
        file_cfg = MailConfig.from_yaml(config_path)
    except FileNotFoundError:
        raise ConfigurationError(f"Config file not found: {config_path}") from None

    # — env vars override file values field-by-field —
    return _merge_env(file_cfg)


def load_llm() -> str:
    """Resolve the LLM API key through the same cascade as :func:`load`,
    but *without* requiring the mail fields.

    Order: ``LLM_API_KEY`` environment variable wins; otherwise the
    ``llm.api_key`` field of the YAML config file at ``MAIL_CONFIG_PATH``
    (default ``config/mail.local.yaml``) is consulted.

    This is separated from :func:`load` because ``detect`` runs before a
    complete mail configuration exists — it only needs the LLM settings.
    """
    api_key = os.environ.get("LLM_API_KEY", "")

    if not api_key:
        config_path = Path(os.environ.get("MAIL_CONFIG_PATH", DEFAULT_CONFIG_PATH))
        if config_path.exists():
            try:
                # Read the default (or first) account's ``llm:`` section. A
                # deprecated mono file is routed through the compat path (which
                # also emits a deprecation warning).
                accounts = MailAccountsConfig.from_yaml(config_path, validate=False)
                file_cfg: MailConfig | None = accounts.default.config
            except (ConfigurationError, FileNotFoundError, OSError):
                file_cfg = None
            if file_cfg is not None:
                api_key = api_key or file_cfg.llm_api_key

    return api_key


def _merge_env(base: MailConfig) -> MailConfig:
    """Return a new ``MailConfig`` where any set env var overrides *base*."""
    kwargs: dict[str, Any] = {}
    for spec in _FIELD_SPECS:
        raw = os.environ.get(spec.env_key, "")
        if raw:
            if spec.kind == "int":
                kwargs[spec.field_name] = _parse_int(spec.env_key, raw)
            elif spec.kind == "bool":
                kwargs[spec.field_name] = _parse_bool(spec.env_key, raw)
            elif spec.kind == "tls_mode":
                _check_tls_mode(spec.env_key, raw)
                kwargs[spec.field_name] = raw
            else:
                kwargs[spec.field_name] = raw
        else:
            kwargs[spec.field_name] = getattr(base, spec.field_name)
    return MailConfig(**kwargs)


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


# ---------------------------------------------------------------------------
# Shared per-field environment parsing
# ---------------------------------------------------------------------------


def _build_config_from_env(
    raw_for: Callable[[_FieldSpec], str],
    label_for: Callable[[_FieldSpec], str],
) -> MailConfig:
    """Build a ``MailConfig`` from a per-field environment source.

    ``raw_for(spec)`` returns the raw string value for a field (``""`` when
    absent); ``label_for(spec)`` returns the variable name used in error
    messages.  Factored out so both ``MailConfig.from_env`` and the
    namespaced multi-account loader (``MailAccountsConfig.from_env``) share
    exactly the same required-field / default / coercion / validation logic
    rather than duplicating it.
    """
    missing: list[str] = []
    errors: list[str] = []
    kwargs: dict[str, Any] = {}

    for spec in _FIELD_SPECS:
        raw = raw_for(spec)
        label = label_for(spec)
        if not raw:
            if spec.required_in_env:
                missing.append(label)
                kwargs[spec.field_name] = ""
            else:
                kwargs[spec.field_name] = spec.default
            continue
        if spec.kind == "str":
            kwargs[spec.field_name] = raw
        elif spec.kind == "int":
            try:
                kwargs[spec.field_name] = int(raw)
            except ValueError:
                errors.append(f"{label} must be an integer, got {raw!r}")
                kwargs[spec.field_name] = spec.default
        elif spec.kind == "bool":
            try:
                kwargs[spec.field_name] = _parse_bool(label, raw)
            except ConfigurationError as exc:
                errors.append(exc.message)
                kwargs[spec.field_name] = spec.default
        else:  # "tls_mode"
            if raw not in _VALID_TLS_MODES:
                errors.append(
                    f"{label} must be one of {sorted(_VALID_TLS_MODES)!r}, got {raw!r}"
                )
            kwargs[spec.field_name] = raw

    # -- final validation --------------------------------------------------

    msgs: list[str] = []
    if missing:
        msgs.append(
            "Missing required environment variable(s): " + ", ".join(sorted(missing))
        )
    msgs.extend(errors)
    if msgs:
        # If *only* missing-required-field errors (no invalid values), flag
        # the error so load()/load_accounts() can safely fall back to the
        # YAML file.  Invalid values mean the user explicitly set an env var
        # — falling back would silently swallow their typo.
        raise ConfigurationError(
            "\n".join(msgs),
            missing_only=bool(missing and not errors),
        )

    return MailConfig(**kwargs)


# ---------------------------------------------------------------------------
# Multi-account model
# ---------------------------------------------------------------------------

# Per-account stable identifier charset.  It is used in SQLite filenames and
# (later in the epic) in URLs / board selectors, so keep it filesystem- and
# URL-safe.
_ACCOUNT_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]+$")

# Prefix for the namespaced multi-account environment scheme.
_ENV_ACCOUNTS_PREFIX = "MAIL_ACCOUNTS_"

# Matches ``MAIL_ACCOUNTS_<n>_<FIELD>`` and captures the integer index.
_ENV_ACCOUNT_INDEX_RE: Final[re.Pattern[str]] = re.compile(r"^MAIL_ACCOUNTS_(\d+)_")


@dataclasses.dataclass(frozen=True)
class MailAccount:
    """One named mailbox: a stable ``account_id`` plus its ``MailConfig``.

    ``label`` is an optional human-friendly display name.  ``account_id`` is
    a stable identifier (e.g. ``"personal"``) used in the account's SQLite
    filename and, later in the epic, in URLs / board selectors — so it must
    be non-empty and match ``^[A-Za-z0-9._-]+$``.
    """

    account_id: str
    config: MailConfig
    label: str | None = None

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ConfigurationError("account_id must be non-empty")
        if not _ACCOUNT_ID_RE.match(self.account_id):
            raise ConfigurationError(
                f"account_id {self.account_id!r} must match {_ACCOUNT_ID_RE.pattern!r}"
            )


@dataclasses.dataclass(frozen=True)
class MailAccountsConfig:
    """An ordered collection of :class:`MailAccount`s plus a default id.

    One SQLite DB per account
    -------------------------
    Multiple accounts are modelled as N independent :class:`MailConfig`
    instances, each carrying its **own** ``db_path``, rather than adding an
    ``account_id`` column to every table.  The rationale:

    - Per-account state (triage decisions, ``SenderMemory``, archive
      watermarks — all keyed by ``message_id`` in each DB) is naturally
      isolated with zero schema migration.
    - Each :class:`MailConfig` already owns a ``db_path`` field, so no new
      per-row plumbing is required.
    - The cost is one SQLite file per account; uniqueness of ``db_path``
      across accounts is therefore enforced at load time (see
      ``__post_init__``).

    Validation (all raise :class:`ConfigurationError`): at least one
    account; all ``account_id``s unique; all ``MailConfig.db_path``s unique
    across accounts; ``default_account_id`` resolves to a known account.
    """

    accounts: tuple[MailAccount, ...]
    default_account_id: str

    def __post_init__(self) -> None:
        if not self.accounts:
            raise ConfigurationError("at least one account is required")

        ids = [account.account_id for account in self.accounts]
        duplicate_ids = sorted({i for i in ids if ids.count(i) > 1})
        if duplicate_ids:
            raise ConfigurationError(f"duplicate account_id(s): {duplicate_ids!r}")

        db_paths = [account.config.db_path for account in self.accounts]
        duplicate_paths = sorted({p for p in db_paths if db_paths.count(p) > 1})
        if duplicate_paths:
            raise ConfigurationError(
                f"duplicate db_path(s) across accounts: {duplicate_paths!r}"
            )

        if self.default_account_id not in ids:
            raise ConfigurationError(
                f"default_account_id {self.default_account_id!r} is not one "
                f"of the configured accounts: {ids!r}"
            )

    def get(self, account_id: str) -> MailAccount:
        """Return the account with *account_id*.

        Raises:
            ConfigurationError: When no account matches (the message lists
                the valid ids).
        """
        for account in self.accounts:
            if account.account_id == account_id:
                return account
        raise ConfigurationError(
            f"unknown account_id {account_id!r}; valid ids: {list(self.ids())!r}"
        )

    @property
    def default(self) -> MailAccount:
        """Return the :class:`MailAccount` for ``default_account_id``."""
        return self.get(self.default_account_id)

    def ids(self) -> tuple[str, ...]:
        """Return the ordered tuple of account ids."""
        return tuple(account.account_id for account in self.accounts)

    # -- loaders -----------------------------------------------------------

    @classmethod
    def from_yaml(
        cls, path: str | Path, *, validate: bool = True
    ) -> MailAccountsConfig:
        """Build a ``MailAccountsConfig`` from a YAML file.

        Two shapes are recognised:

        * **Multi-account** — a top-level ``accounts:`` list.  Each entry is
          a mapping with ``id`` (required str), optional ``label`` (str) and
          the usual nested config sections (``imap``, ``smtp``, ``auth``,
          ``store``, ``llm``, ``ingest``, ``archive``, ``triage``) parsed by
          the same helper as the single-account loader.  An optional
          top-level ``default_account:`` names the default (absent → the
          first entry).  When an entry omits ``store.path`` the per-account
          default ``".data/<id>/mail.db"`` is used so DBs never collide.
        * **Legacy single-account** — no top-level ``accounts:`` key.  The
          whole file is parsed via :meth:`MailConfig.from_yaml` and wrapped
          in a one-element container with ``account_id="default"`` (keeping
          the historical ``".data/mail.db"`` default).

        ``validate=False`` skips per-account required-field checks (mirroring
        :meth:`MailConfig._parse_config_dict`) but still enforces id /
        db_path uniqueness.

        Raises:
            ConfigurationError: On invalid structure or failed validation.
            FileNotFoundError: If *path* does not exist.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        try:
            data = read_yaml_file(path)
        except YamlConfigError as exc:
            raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc

        accounts_raw = data.get("accounts") if isinstance(data, dict) else None
        if accounts_raw is None:
            # Legacy single-account shape — reuse the existing loader verbatim.
            # Deprecated: emit a migration signal but keep loading successfully.
            _warn_deprecated_mono_shape(f"Config {path}")
            cfg = MailConfig.from_yaml(path, validate=validate)
            return cls(
                accounts=(MailAccount(account_id="default", config=cfg, label=None),),
                default_account_id="default",
            )

        if not isinstance(accounts_raw, list):
            raise ConfigurationError("Config key 'accounts' must be a list")
        if not accounts_raw:
            raise ConfigurationError("'accounts' must contain at least one account")

        accounts: list[MailAccount] = []
        for entry in accounts_raw:
            if not isinstance(entry, dict):
                raise ConfigurationError("each 'accounts' entry must be a mapping")
            raw_id = entry.get("id")
            if not isinstance(raw_id, str) or not raw_id:
                raise ConfigurationError(
                    "each account requires a non-empty string 'id'"
                )
            raw_label = entry.get("label")
            if raw_label is not None and not isinstance(raw_label, str):
                raise ConfigurationError(f"account {raw_id!r} 'label' must be a string")
            store_section = entry.get("store")
            has_store_path = isinstance(store_section, dict) and "path" in store_section
            cfg = MailConfig._parse_config_dict(entry, path, validate=validate)
            if not has_store_path:
                cfg = dataclasses.replace(cfg, db_path=f".data/{raw_id}/mail.db")
            accounts.append(MailAccount(account_id=raw_id, config=cfg, label=raw_label))

        raw_default = data.get("default_account")
        if raw_default is None:
            default_id = accounts[0].account_id
        elif isinstance(raw_default, str):
            default_id = raw_default
        else:
            raise ConfigurationError("'default_account' must be a string")

        return cls(accounts=tuple(accounts), default_account_id=default_id)

    @classmethod
    def from_env(cls) -> MailAccountsConfig:
        """Build a ``MailAccountsConfig`` from environment variables.

        Two shapes are recognised:

        * **Namespaced multi-account** — any ``MAIL_ACCOUNTS_<n>_*`` var is
          present.  For each contiguous index ``n`` starting at 0, one
          account is built from the namespaced vars.  A field whose
          single-account env var is ``MAIL_<X>`` becomes
          ``MAIL_ACCOUNTS_<n>_<X>``; the two LLM fields become
          ``MAIL_ACCOUNTS_<n>_LLM_API_KEY``.  Two extra
          vars: ``MAIL_ACCOUNTS_<n>_ID`` (required) and
          ``MAIL_ACCOUNTS_<n>_LABEL`` (optional).  A missing ``store.path``
          yields the per-account default ``".data/<id>/mail.db"``.  An
          optional ``MAIL_ACCOUNTS_DEFAULT`` names the default id.  A gap in
          the index sequence raises :class:`ConfigurationError`.
        * **Legacy single-account** — no ``MAIL_ACCOUNTS_*`` vars.  Delegates
          to :meth:`MailConfig.from_env` and wraps the result as the
          one-element ``"default"`` container.

        Raises:
            ConfigurationError: On invalid values, an id gap, or an invalid
                account id.
        """
        present_indices = sorted(
            {
                int(match.group(1))
                for key in os.environ
                if (match := _ENV_ACCOUNT_INDEX_RE.match(key))
            }
        )
        if not present_indices:
            cfg = MailConfig.from_env()
            # Deprecated: a complete single-account env was supplied. Keep
            # loading but steer the operator to the multi-account scheme.
            _warn_deprecated_mono_shape("The environment (MAIL_* variables)")
            return cls(
                accounts=(MailAccount(account_id="default", config=cfg, label=None),),
                default_account_id="default",
            )

        accounts: list[MailAccount] = []
        index = 0
        while any(
            key.startswith(f"{_ENV_ACCOUNTS_PREFIX}{index}_") for key in os.environ
        ):
            accounts.append(_build_account_from_env(index))
            index += 1

        # Contiguity: every present index must be < the count we consumed.
        if present_indices[-1] >= index:
            raise ConfigurationError(
                f"non-contiguous MAIL_ACCOUNTS_* indices: consumed 0..{index - 1} "
                f"but index {present_indices[-1]} is also set (gap before it)"
            )

        raw_default = os.environ.get("MAIL_ACCOUNTS_DEFAULT")
        default_id = raw_default if raw_default else accounts[0].account_id
        return cls(accounts=tuple(accounts), default_account_id=default_id)


def _build_account_from_env(index: int) -> MailAccount:
    """Build one :class:`MailAccount` from ``MAIL_ACCOUNTS_<index>_*`` vars."""
    prefix = f"{_ENV_ACCOUNTS_PREFIX}{index}_"

    def namespaced(env_key: str) -> str:
        suffix = env_key[len("MAIL_") :] if env_key.startswith("MAIL_") else env_key
        return f"{prefix}{suffix}"

    cfg = _build_config_from_env(
        lambda spec: os.environ.get(namespaced(spec.env_key), ""),
        lambda spec: namespaced(spec.env_key),
    )

    account_id = os.environ.get(f"{prefix}ID", "")
    if not os.environ.get(namespaced("MAIL_DB_PATH")):
        cfg = dataclasses.replace(cfg, db_path=f".data/{account_id}/mail.db")
    raw_label = os.environ.get(f"{prefix}LABEL")
    label = raw_label if raw_label else None
    return MailAccount(account_id=account_id, config=cfg, label=label)


def load_accounts() -> MailAccountsConfig:
    """Load ``MailAccountsConfig`` through the same cascade as :func:`load`.

    1.  Call :meth:`MailAccountsConfig.from_env`.  If the environment fully
        describes the accounts (namespaced multi-account, or a complete
        single-account env), return immediately — env wins.
    2.  Otherwise, if *only* required fields are missing (no invalid values),
        fall back to the YAML config file at ``MAIL_CONFIG_PATH`` (default
        ``config/mail.local.yaml``).  A multi-account file is parsed directly;
        a legacy single-account file is routed through :func:`load` so that
        ``MAIL_*`` env vars still override its values field-by-field
        (preserving today's single-account behaviour) before being wrapped in
        the one-element ``"default"`` container.

    If :meth:`MailAccountsConfig.from_env` fails because of an *invalid* value
    (e.g. a non-integer port), the error is re-raised immediately rather than
    silently falling back to the file.
    """
    try:
        return MailAccountsConfig.from_env()
    except ConfigurationError as exc:
        if not exc.missing_only:
            raise

    config_path = Path(os.environ.get("MAIL_CONFIG_PATH", DEFAULT_CONFIG_PATH))
    if config_path.exists():
        try:
            data = read_yaml_file(config_path)
        except YamlConfigError as exc:
            raise ConfigurationError(f"Invalid YAML in {config_path}: {exc}") from exc
    else:
        data = {}

    if isinstance(data, dict) and isinstance(data.get("accounts"), list):
        return MailAccountsConfig.from_yaml(config_path)

    # Legacy single-account fallback: resolve the mono config (so _merge_env
    # applies env overrides on top of the file exactly as it does today) and
    # wrap it.  Deprecated — emit a migration signal naming the source.
    cfg = _load_mono_config()
    source = f"Config {config_path}" if config_path.exists() else "The environment"
    _warn_deprecated_mono_shape(source)
    return MailAccountsConfig(
        accounts=(MailAccount(account_id="default", config=cfg, label=None),),
        default_account_id="default",
    )


# ---------------------------------------------------------------------------
# Multi-account YAML rendering
# ---------------------------------------------------------------------------


def _yaml_scalar(value: object) -> str:
    """Render *value* as a YAML scalar.

    Booleans and integers are emitted bare; strings are always double-quoted
    (a valid, lossless YAML representation that safely escapes any special
    characters, empty strings, and values that would otherwise be parsed as a
    non-string).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    return json.dumps(str(value))


def _render_account_block(account: MailAccount, indent: str) -> list[str]:
    """Render one :class:`MailAccount` as a YAML list item under ``accounts:``.

    The mandatory ``imap`` / ``smtp`` / ``auth`` / ``store`` sections are
    always emitted; the optional sections (``llm`` / ``ingest`` / ``archive``
    / ``triage`` / ``langfuse`` and the OAuth2 fields) are emitted only when
    they carry a non-default value, so freshly-detected configs stay terse
    while migrated configs preserve any customised value.
    """
    cfg = account.config
    defaults = MailConfig(imap_host="", smtp_host="", username="", password="")
    item = indent + "  "
    lines = [f"{indent}- id: {_yaml_scalar(account.account_id)}"]
    if account.label:
        lines.append(f"{item}label: {_yaml_scalar(account.label)}")
    lines.append(f"{item}imap:")
    lines.append(f"{item}  host: {_yaml_scalar(cfg.imap_host)}")
    lines.append(f"{item}  port: {cfg.imap_port}")
    lines.append(f"{item}  tls_mode: {_yaml_scalar(cfg.imap_tls_mode)}")
    lines.append(f"{item}  folder: {_yaml_scalar(cfg.imap_folder)}")
    lines.append(f"{item}smtp:")
    lines.append(f"{item}  host: {_yaml_scalar(cfg.smtp_host)}")
    lines.append(f"{item}  port: {cfg.smtp_port}")
    lines.append(f"{item}  tls_mode: {_yaml_scalar(cfg.smtp_tls_mode)}")
    lines.append(f"{item}auth:")
    lines.append(f"{item}  username: {_yaml_scalar(cfg.username)}")
    lines.append(f"{item}  password: {_yaml_scalar(cfg.password)}")
    if cfg.oauth2_token:
        lines.append(f"{item}  oauth2_token: {_yaml_scalar(cfg.oauth2_token)}")
    if cfg.oauth2_client_id:
        lines.append(f"{item}  oauth2_client_id: {_yaml_scalar(cfg.oauth2_client_id)}")
    if cfg.oauth2_client_secret:
        lines.append(
            f"{item}  oauth2_client_secret: {_yaml_scalar(cfg.oauth2_client_secret)}"
        )
    lines.append(f"{item}store:")
    lines.append(f"{item}  path: {_yaml_scalar(cfg.db_path)}")
    if cfg.llm_api_key:
        lines.append(f"{item}llm:")
        lines.append(f"{item}  api_key: {_yaml_scalar(cfg.llm_api_key)}")
    if cfg.ingest_interval_minutes != defaults.ingest_interval_minutes:
        lines.append(f"{item}ingest:")
        lines.append(f"{item}  interval_minutes: {cfg.ingest_interval_minutes}")
    if (
        cfg.archive_root != defaults.archive_root
        or cfg.archive_namespace != defaults.archive_namespace
        or cfg.archive_enabled != defaults.archive_enabled
    ):
        lines.append(f"{item}archive:")
        lines.append(f"{item}  root: {_yaml_scalar(cfg.archive_root)}")
        lines.append(f"{item}  namespace: {_yaml_scalar(cfg.archive_namespace)}")
        lines.append(f"{item}  enabled: {_yaml_scalar(cfg.archive_enabled)}")
    if cfg.triage_on_ingest != defaults.triage_on_ingest:
        lines.append(f"{item}triage:")
        lines.append(f"{item}  on_ingest: {_yaml_scalar(cfg.triage_on_ingest)}")
    if cfg.langfuse_public_key or cfg.langfuse_secret_key or cfg.langfuse_base_url:
        lines.append(f"{item}langfuse:")
        lines.append(f"{item}  public_key: {_yaml_scalar(cfg.langfuse_public_key)}")
        lines.append(f"{item}  secret_key: {_yaml_scalar(cfg.langfuse_secret_key)}")
        lines.append(f"{item}  base_url: {_yaml_scalar(cfg.langfuse_base_url)}")
    return lines


def render_accounts_yaml(
    accounts: Sequence[MailAccount],
    default_account_id: str,
    *,
    banner: str = "",
) -> str:
    """Render *accounts* as a multi-account YAML config file.

    Emits a top-level ``default_account:`` followed by an ``accounts:`` list.
    Used by ``detect`` (to write/append a detected account) and by
    ``migrate-config`` (to convert a deprecated single-account file).
    """
    lines: list[str] = []
    if banner:
        lines.append(banner.rstrip("\n"))
        lines.append("")
    lines.append(f"default_account: {_yaml_scalar(default_account_id)}")
    lines.append("")
    lines.append("accounts:")
    for account in accounts:
        lines.extend(_render_account_block(account, "  "))
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"
