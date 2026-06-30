"""Configuration model: the ``MailConfig`` / ``MailAccount`` dataclasses.

Holds the immutable configuration dataclasses and their ``from_env`` /
``from_yaml`` loaders, plus the per-field environment build helpers that
construct them.  Depends on :mod:`robotsix_auto_mail.config.schema` for the
error type, validation constants and the field-spec table.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from robotsix_yaml_config import (
    YamlConfigError,
    read_yaml_file,
)

from robotsix_auto_mail.config.schema import (
    _FIELD_SPECS,
    _REQUIRED,
    _VALID_LOG_FORMATS,
    _VALID_LOG_LEVELS,
    _VALID_TLS_MODES,
    DEFAULT_ARCHIVE_ROOT,
    DEFAULT_DB_PATH,
    DEFAULT_IMAP_TLS_MODE,
    DEFAULT_INGEST_INTERVAL_MINUTES,
    DEFAULT_SMTP_TLS_MODE,
    ConfigurationError,
    _FieldSpec,
    _get_int,
    _get_str,
    _get_table,
    _mono_shape_error,
    _parse_bool,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Template-literal guard — catches unsubstituted values like
# ``"{accounts.4.auth.username}"`` before they hit the network.
# ---------------------------------------------------------------------------

_TEMPLATE_LITERAL_RE: Final[re.Pattern[str]] = re.compile(r"\{[^}]+\}")
_TEMPLATE_CHECKED_FIELDS: Final[tuple[str, ...]] = (
    "imap_host",
    "smtp_host",
    "username",
    "password",
    "imap_folder",
)


def _validate_template_literals(cfg: MailConfig) -> None:
    """Raise ``ConfigurationError`` if any required connection field on *cfg*
    contains an unsubstituted ``{...}`` template pattern."""
    for field_name in _TEMPLATE_CHECKED_FIELDS:
        value = getattr(cfg, field_name, "")
        if value and _TEMPLATE_LITERAL_RE.search(value):
            if field_name == "password":
                display = "<redacted>"
            else:
                display = repr(value)
            raise ConfigurationError(
                f"Config field '{field_name}' contains an unsubstituted "
                f"template literal: {display}. "
                f"Check your config rendering pipeline."
            )


# Prefix for the namespaced multi-account environment scheme.
_ENV_ACCOUNTS_PREFIX = "MAIL_ACCOUNTS_"

# Matches ``MAIL_ACCOUNTS_<n>_<FIELD>`` and captures the integer index.
_ENV_ACCOUNT_INDEX_RE: Final[re.Pattern[str]] = re.compile(r"^MAIL_ACCOUNTS_(\d+)_")


# ---------------------------------------------------------------------------
# Failed-account tracking — records an account that failed validation at
# config-load time so the board can degrade gracefully.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class FailedAccountEntry:
    """Records an account that failed validation at config-load time."""

    account_id: str  # raw ``id:`` field from YAML / env, or ``'<account-N>'`` fallback
    error: str  # the ConfigurationError message


# ---------------------------------------------------------------------------
# Shared field-coercion helper
# ---------------------------------------------------------------------------


def _coerce_field(spec: _FieldSpec, raw: str, label: str) -> tuple[Any, str | None]:
    """Coerce and validate a raw string value according to *spec.kind*.

    Returns ``(value, error_message)``.  When *error_message* is not
    ``None``, *value* is the default to fall back to.
    """
    kind = spec.kind
    if kind == "str":
        return raw, None
    elif kind == "int":
        try:
            return int(raw), None
        except ValueError:
            return spec.default, f"{label} must be an integer, got {raw!r}"
    elif kind == "bool":
        try:
            return _parse_bool(label, raw), None
        except ConfigurationError as exc:
            return spec.default, exc.message
    elif kind == "tls_mode":
        if raw not in _VALID_TLS_MODES:
            return raw, (
                f"{label} must be one of {sorted(_VALID_TLS_MODES)!r}, got {raw!r}"
            )
        return raw, None
    elif kind == "log_level":
        if raw.upper() not in _VALID_LOG_LEVELS:
            return raw, (
                f"{label} must be one of {sorted(_VALID_LOG_LEVELS)!r}, got {raw!r}"
            )
        return raw, None
    elif kind == "log_format":
        if raw.lower() not in _VALID_LOG_FORMATS:
            return raw, (
                f"{label} must be one of {sorted(_VALID_LOG_FORMATS)!r}, got {raw!r}"
            )
        return raw, None
    else:
        return raw, None


# ---------------------------------------------------------------------------
# Shared YAML-reader helper
# ---------------------------------------------------------------------------


def _read_config_yaml(path: str | Path) -> dict[str, Any]:
    """Open *path*, validate its existence, and return the parsed YAML dict.

    ``read_yaml_file`` returns an empty dict for a missing file; we
    still want ``from_yaml`` to surface a ``FileNotFoundError`` so
    callers (e.g. ``load()``) can distinguish "no file" from "empty file".
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    try:
        data: dict[str, Any] = read_yaml_file(path)
        return data
    except YamlConfigError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc


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
    llm_provider_model: str = ""

    # Minutes between automatic ingest cycles (`ingest --watch`).
    ingest_interval_minutes: int = DEFAULT_INGEST_INTERVAL_MINUTES

    # Self-managed archive folder structure.
    archive_root: str = DEFAULT_ARCHIVE_ROOT
    archive_enabled: bool = True

    # Run the inbox triage agent automatically at the end of each ingest.
    triage_on_ingest: bool = True

    # Whether the component-agent HTTP API (monitor / config-get / config-set)
    # is served on the board server.
    component_agent_enabled: bool = False

    # OAuth2 / XOAUTH2 credentials (Gmail, Microsoft 365, etc.).
    # Optional; when ``oauth2_token`` is set, SASL XOAUTH2 is used
    # instead of password-based ``login()``.
    oauth2_token: str = ""
    oauth2_client_id: str = ""
    oauth2_client_secret: str = ""

    # MSAL-managed OAuth2 (Microsoft 365). When ``oauth2_provider`` is set
    # to ``"microsoft"``, access tokens are acquired and refreshed via MSAL
    # instead of password/static-token auth. ``oauth2_tenant`` is the Azure
    # AD tenant (default ``organizations``).
    oauth2_provider: str = ""
    oauth2_tenant: str = "organizations"

    # Langfuse observability — optional; when public_key/secret_key are set,
    # every LLM agent run is traced to the configured Langfuse project.
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""

    # Logging configuration — application-wide (global).
    log_level: str = "INFO"
    log_format: str = "console"
    log_file_dir: str = ".mail_log"

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
        cfg = _build_config_from_env(
            lambda spec: os.environ.get(spec.env_key, ""),
            lambda spec: spec.env_key,
        )
        _validate_template_literals(cfg)
        return cfg

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

            raw = section.get(key_name)
            if raw is None:
                kwargs[spec.field_name] = (
                    "" if spec.default is _REQUIRED else spec.default
                )
                continue

            # Reject non-scalar YAML values for config fields.
            if isinstance(raw, (dict, list)):
                errors.append(
                    f"{spec.yaml_path} must be a scalar value, got {type(raw).__name__}"
                )
                kwargs[spec.field_name] = spec.default
                continue

            raw_str = str(raw) if not isinstance(raw, str) else raw
            value, err = _coerce_field(spec, raw_str, spec.yaml_path)
            kwargs[spec.field_name] = value
            if err:
                errors.append(err)

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
        data = _read_config_yaml(path)
        cfg = cls._parse_config_dict(data, path, validate=validate)
        _validate_template_literals(cfg)
        return cfg


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
# Shared per-field environment parsing
# ---------------------------------------------------------------------------


def _build_config_from_env(
    raw_for: Callable[[_FieldSpec], str],
    label_for: Callable[[_FieldSpec], str],
    *,
    skip_global: bool = False,
) -> MailConfig:
    """Build a ``MailConfig`` from a per-field environment source.

    ``raw_for(spec)`` returns the raw string value for a field (``""`` when
    absent); ``label_for(spec)`` returns the variable name used in error
    messages.  Factored out so both ``MailConfig.from_env`` and the
    namespaced multi-account loader (``MailAccountsConfig.from_env``) share
    exactly the same required-field / default / coercion / validation logic
    rather than duplicating it.

    When *skip_global* is True, fields marked ``global_field=True`` are
    skipped — the caller is responsible for populating them separately
    (e.g. from bare environment variables in the multi-account path).
    """
    missing: list[str] = []
    errors: list[str] = []
    kwargs: dict[str, Any] = {}

    for spec in _FIELD_SPECS:
        if skip_global and spec.global_field:
            kwargs[spec.field_name] = spec.default
            continue
        raw = raw_for(spec)
        label = label_for(spec)
        if not raw:
            if spec.required_in_env:
                missing.append(label)
                kwargs[spec.field_name] = ""
            else:
                kwargs[spec.field_name] = spec.default
            continue
        value, err = _coerce_field(spec, raw, label)
        kwargs[spec.field_name] = value
        if err:
            errors.append(err)

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
# Shared section-extraction helper
# ---------------------------------------------------------------------------


def _extract_section_fields(
    data: dict[str, Any],
    section_name: str,
    field_map: list[tuple[str, Callable[..., Any], str, Any]],
    path: Path | None = None,
) -> dict[str, Any]:
    """Extract fields from an optional top-level YAML section.

    Args:
        data: The parsed YAML dict.
        section_name: Top-level key (e.g. ``"llm"``).
        field_map: List of ``(result_key, extractor, yaml_key, default)``
            tuples.  The *extractor* is one of :func:`_get_str`,
            :func:`_get_str` or :func:`_get_int`.
        path: Config file path for error messages (required when
            *field_map* includes :func:`_get_int` entries).

    Returns:
        Dict mapping each *result_key* to the extracted value
        (or its *default* when the section or key is absent).
    """
    section = _get_table(data, section_name)
    result: dict[str, Any] = {}
    for result_key, extractor, yaml_key, default in field_map:
        if section is not None:
            if extractor is _get_int:
                assert path is not None, (  # noqa: S101  # nosec B101
                    "_extract_section_fields: path required for _get_int"
                )
                result[result_key] = extractor(section, yaml_key, default, path)
            else:
                result[result_key] = extractor(section, yaml_key, default)
        else:
            result[result_key] = default
    return result


# ---------------------------------------------------------------------------
# Multi-account model
# ---------------------------------------------------------------------------

# Per-account stable identifier charset.  It is used in SQLite filenames and
# (later in the epic) in URLs / board selectors, so keep it filesystem- and
# URL-safe.
_ACCOUNT_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]+$")


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
    across accounts; ``default_account_id`` resolves to a known account
    and is the account used for CLI commands that accept an optional
    ``--account`` flag (e.g. ``triage``, ``ingest``) when that flag is
    omitted, and for initialising the HTTP server's startup configuration
    (component-agent, initial DB path).  The board view itself always
    defaults to the aggregate (``__all__``) view for multi-account setups
    and does not consult this field.
    """

    accounts: tuple[MailAccount, ...]
    default_account_id: str
    failed_accounts: tuple[FailedAccountEntry, ...] = dataclasses.field(
        default_factory=tuple
    )

    def __post_init__(self) -> None:
        if not self.accounts and not self.failed_accounts:
            raise ConfigurationError("No accounts configured.")
        if not self.accounts:
            raise ConfigurationError(
                "All accounts failed to load:\n"
                + "\n".join(
                    f"  {e.account_id}: {e.error}" for e in self.failed_accounts
                )
            )

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
          ``store``, ``ingest``, ``archive``, ``triage``) parsed by
          the same helper as the single-account loader.  ``llm:`` and
          ``langfuse:`` are **top-level** sections (application-wide); they
          are applied to every account via :func:`dataclasses.replace`.
          An optional top-level ``default_account:`` names the default
          (absent → the first entry).  When an entry omits ``store.path``
          the per-account default ``".data/<id>/mail.db"`` is used so DBs
          never collide.
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
        data = _read_config_yaml(path)

        accounts_raw = data.get("accounts") if isinstance(data, dict) else None
        if accounts_raw is None:
            # The single-account ("mono") YAML file shape is no longer
            # supported — reject it with an actionable error.
            raise ConfigurationError(_mono_shape_error(path))

        if not isinstance(accounts_raw, list):
            raise ConfigurationError("Config key 'accounts' must be a list")
        if not accounts_raw:
            raise ConfigurationError("'accounts' must contain at least one account")

        # -- top-level application-wide sections ---------------------------

        global_llm_api_key: str = ""
        global_llm_provider_model: str = ""
        global_langfuse_public_key: str = ""
        global_langfuse_secret_key: str = ""
        global_langfuse_base_url: str = ""

        if isinstance(data, dict):
            llm = _extract_section_fields(
                data,
                "llm",
                [
                    ("api_key", _get_str, "api_key", ""),
                    ("provider_model", _get_str, "provider_model", ""),
                ],
            )
            global_llm_api_key = llm["api_key"]
            global_llm_provider_model = llm["provider_model"]

            langfuse = _extract_section_fields(
                data,
                "langfuse",
                [
                    ("public_key", _get_str, "public_key", ""),
                    ("secret_key", _get_str, "secret_key", ""),
                    ("base_url", _get_str, "base_url", ""),
                ],
            )
            global_langfuse_public_key = langfuse["public_key"]
            global_langfuse_secret_key = langfuse["secret_key"]
            global_langfuse_base_url = langfuse["base_url"]

        accounts: list[MailAccount] = []
        failed: list[FailedAccountEntry] = []
        for index, entry in enumerate(accounts_raw, start=1):
            if not isinstance(entry, dict):
                raise ConfigurationError("each 'accounts' entry must be a mapping")
            raw_id: str = str(entry.get("id", f"<account-{index}>"))
            try:
                raw_label = entry.get("label")
                if raw_label is not None and not isinstance(raw_label, str):
                    raise ConfigurationError(
                        f"account {raw_id!r} 'label' must be a string"
                    )

                # llm: and langfuse: are now top-level (application-wide);
                # per-account blocks are rejected with an actionable error.
                for section_name in ("llm", "langfuse", "logging"):
                    if section_name in entry:
                        raise ConfigurationError(
                            f"account {raw_id!r} has a per-account "
                            f"{section_name!r} block — {section_name}: is now "
                            f"a top-level section. Move it outside the "
                            f"accounts: list."
                        )

                store_section = entry.get("store")
                has_store_path = (
                    isinstance(store_section, dict) and "path" in store_section
                )
                cfg = MailConfig._parse_config_dict(entry, path, validate=validate)
                _validate_template_literals(cfg)
                if not has_store_path:
                    cfg = dataclasses.replace(cfg, db_path=f".data/{raw_id}/mail.db")

                # Apply top-level llm / langfuse values
                # (global wins over defaults).
                cfg = dataclasses.replace(
                    cfg,
                    llm_api_key=global_llm_api_key or cfg.llm_api_key,
                    llm_provider_model=global_llm_provider_model
                    or cfg.llm_provider_model,
                    langfuse_public_key=global_langfuse_public_key
                    or cfg.langfuse_public_key,
                    langfuse_secret_key=global_langfuse_secret_key
                    or cfg.langfuse_secret_key,
                    langfuse_base_url=global_langfuse_base_url or cfg.langfuse_base_url,
                )

                accounts.append(
                    MailAccount(account_id=raw_id, config=cfg, label=raw_label)
                )
            except ConfigurationError as exc:
                logger.error(
                    "Skipping account %r — invalid config: %s",
                    raw_id,
                    exc,
                )
                failed.append(FailedAccountEntry(account_id=raw_id, error=str(exc)))
                continue

        raw_default = data.get("default_account")
        if raw_default is None:
            default_id = accounts[0].account_id if accounts else ""
        elif isinstance(raw_default, str):
            default_id = raw_default
        else:
            raise ConfigurationError("'default_account' must be a string")

        return cls(
            accounts=tuple(accounts),
            default_account_id=default_id,
            failed_accounts=tuple(failed),
        )

    @classmethod
    def from_env(cls) -> MailAccountsConfig:
        """Build a ``MailAccountsConfig`` from environment variables.

        Two shapes are recognised:

        * **Namespaced multi-account** — any ``MAIL_ACCOUNTS_<n>_*`` var is
          present.  For each contiguous index ``n`` starting at 0, one
          account is built from the namespaced vars.  A field whose
          single-account env var is ``MAIL_<X>`` becomes
          ``MAIL_ACCOUNTS_<n>_<X>``.  ``LLM_API_KEY``, ``LLM_PROVIDER_MODEL``,
          and ``LANGFUSE_*`` are application-wide (global) and read from
          the bare env vars, not namespaced.  Two extra
          vars: ``MAIL_ACCOUNTS_<n>_ID`` (required) and
          ``MAIL_ACCOUNTS_<n>_LABEL`` (optional).  A missing ``store.path``
          yields the per-account default ``".data/<id>/mail.db"``.  An
          optional ``MAIL_ACCOUNTS_DEFAULT`` names the default id.  A gap in
          the index sequence raises :class:`ConfigurationError`.
        * **Single-account** — no ``MAIL_ACCOUNTS_*`` vars.  Delegates
          to :meth:`MailConfig.from_env` and wraps the result as the
          one-element ``"default"`` container (a supported isolated-boot
          mechanism, loaded silently).

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
            # Single-account ``MAIL_*`` env is a supported isolated-boot
            # mechanism — load it silently as the one-element "default"
            # container.
            cfg = MailConfig.from_env()
            return cls(
                accounts=(MailAccount(account_id="default", config=cfg, label=None),),
                default_account_id="default",
            )

        accounts: list[MailAccount] = []
        failed: list[FailedAccountEntry] = []
        index = 0
        while any(
            key.startswith(f"{_ENV_ACCOUNTS_PREFIX}{index}_") for key in os.environ
        ):
            raw_id = os.environ.get(
                f"{_ENV_ACCOUNTS_PREFIX}{index}_ID", f"<account-{index}>"
            )
            try:
                accounts.append(_build_account_from_env(index))
            except ConfigurationError as exc:
                logger.error(
                    "Skipping account %r — invalid config: %s",
                    raw_id,
                    exc,
                )
                failed.append(FailedAccountEntry(account_id=raw_id, error=str(exc)))
            index += 1

        # Contiguity: every present index must be < the count we consumed.
        if present_indices[-1] >= index:
            raise ConfigurationError(
                f"non-contiguous MAIL_ACCOUNTS_* indices: consumed 0..{index - 1} "
                f"but index {present_indices[-1]} is also set (gap before it)"
            )

        raw_default = os.environ.get("MAIL_ACCOUNTS_DEFAULT")
        default_id = (
            raw_default if raw_default else (accounts[0].account_id if accounts else "")
        )
        return cls(
            accounts=tuple(accounts),
            default_account_id=default_id,
            failed_accounts=tuple(failed),
        )


def _build_account_from_env(index: int) -> MailAccount:
    """Build one :class:`MailAccount` from ``MAIL_ACCOUNTS_<index>_*`` vars."""
    prefix = f"{_ENV_ACCOUNTS_PREFIX}{index}_"

    def namespaced(env_key: str) -> str:
        suffix = env_key[len("MAIL_") :] if env_key.startswith("MAIL_") else env_key
        return f"{prefix}{suffix}"

    cfg = _build_config_from_env(
        lambda spec: os.environ.get(namespaced(spec.env_key), ""),
        lambda spec: namespaced(spec.env_key),
        skip_global=True,
    )
    _validate_template_literals(cfg)

    # Global fields are application-wide; read them from bare (non-namespaced)
    # env vars.  They were skipped in _build_config_from_env above.
    cfg = dataclasses.replace(
        cfg,
        llm_api_key=os.environ.get("LLM_API_KEY", cfg.llm_api_key),
        llm_provider_model=os.environ.get("LLM_PROVIDER_MODEL", cfg.llm_provider_model),
        langfuse_public_key=os.environ.get(
            "LANGFUSE_PUBLIC_KEY", cfg.langfuse_public_key
        ),
        langfuse_secret_key=os.environ.get(
            "LANGFUSE_SECRET_KEY", cfg.langfuse_secret_key
        ),
        langfuse_base_url=os.environ.get("LANGFUSE_BASE_URL", cfg.langfuse_base_url),
        log_level=os.environ.get("LOG_LEVEL", cfg.log_level),
        log_format=os.environ.get("LOG_FORMAT", cfg.log_format),
        log_file_dir=os.environ.get("LOG_FILE_DIR", cfg.log_file_dir),
    )

    # Validate global logging fields (skipped by _build_config_from_env).
    errs: list[str] = []
    raw_lvl = os.environ.get("LOG_LEVEL", "")
    if raw_lvl and raw_lvl.upper() not in _VALID_LOG_LEVELS:
        errs.append(
            f"LOG_LEVEL must be one of {sorted(_VALID_LOG_LEVELS)!r}, got {raw_lvl!r}"
        )
    raw_fmt = os.environ.get("LOG_FORMAT", "")
    if raw_fmt and raw_fmt.lower() not in _VALID_LOG_FORMATS:
        errs.append(
            f"LOG_FORMAT must be one of {sorted(_VALID_LOG_FORMATS)!r}, got {raw_fmt!r}"
        )
    if errs:
        raise ConfigurationError("\n".join(errs))

    account_id = os.environ.get(f"{prefix}ID", "")
    if not os.environ.get(namespaced("MAIL_DB_PATH")):
        cfg = dataclasses.replace(cfg, db_path=f".data/{account_id}/mail.db")
    raw_label = os.environ.get(f"{prefix}LABEL")
    label = raw_label if raw_label else None
    return MailAccount(account_id=account_id, config=cfg, label=label)
