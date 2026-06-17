"""Configuration model: the ``MailConfig`` / ``MailAccount`` dataclasses.

Holds the immutable configuration dataclasses and their ``from_env`` /
``from_yaml`` loaders, plus the per-field environment build helpers that
construct them.  Depends on :mod:`robotsix_auto_mail.config.schema` for the
error type, validation constants and the field-spec table.
"""

from __future__ import annotations

import dataclasses
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
    _VALID_CALENDAR_TRANSPORTS,
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
    _get_bool,
    _get_int,
    _get_str,
    _get_table,
    _mono_shape_error,
    _parse_bool,
)

# Prefix for the namespaced multi-account environment scheme.
_ENV_ACCOUNTS_PREFIX = "MAIL_ACCOUNTS_"

# Matches ``MAIL_ACCOUNTS_<n>_<FIELD>`` and captures the integer index.
_ENV_ACCOUNT_INDEX_RE: Final[re.Pattern[str]] = re.compile(r"^MAIL_ACCOUNTS_(\d+)_")


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
    llm_provider: str = "openrouter-deepseek"

    # Minutes between automatic ingest cycles (`ingest --watch`).
    ingest_interval_minutes: int = DEFAULT_INGEST_INTERVAL_MINUTES

    # Self-managed archive folder structure.
    archive_root: str = DEFAULT_ARCHIVE_ROOT
    archive_namespace: str = ""
    archive_enabled: bool = True

    # Run the inbox triage agent automatically at the end of each ingest.
    triage_on_ingest: bool = True

    # Whether the 'Add to Calendar' button is rendered in the detail view
    # and dispatch to the robotsix-calendar agent is attempted.
    calendar_enabled: bool = True

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

    # Board agent — optional agent-comm bridge to the mill board.
    # Disabled by default; enable to let other agents drive the board
    # programmatically via agent-comm messages.
    board_agent_enabled: bool = False
    board_agent_api_url: str = ""
    board_agent_api_token: str = ""
    board_agent_repo_id: str = ""
    board_agent_write_ops: bool = True

    # Calendar (Add to Calendar) — agent-comm dispatch transport.
    calendar_transport: str = "in-process"
    calendar_broker_host: str = ""
    calendar_broker_port: int = 8443
    calendar_broker_tls_ca: str = ""
    calendar_broker_client_cert: str = ""
    calendar_broker_client_key: str = ""
    calendar_broker_token: str = ""

    # -- masking -----------------------------------------------------------

    _SECRET_FIELDS = (
        "password",
        "llm_api_key",
        "oauth2_token",
        "oauth2_client_secret",
        "langfuse_secret_key",
        "board_agent_api_token",
        "calendar_broker_token",
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
            elif spec.kind == "log_level":
                value = _get_str(section, key_name, spec.default)
                if value.upper() not in _VALID_LOG_LEVELS:
                    errors.append(
                        f"{spec.yaml_path} must be one of "
                        f"{sorted(_VALID_LOG_LEVELS)!r}, got {value!r}"
                    )
                kwargs[spec.field_name] = value
            elif spec.kind == "log_format":
                value = _get_str(section, key_name, spec.default)
                if value.lower() not in _VALID_LOG_FORMATS:
                    errors.append(
                        f"{spec.yaml_path} must be one of "
                        f"{sorted(_VALID_LOG_FORMATS)!r}, got {value!r}"
                    )
                kwargs[spec.field_name] = value
            elif spec.kind == "calendar_transport":
                value = _get_str(section, key_name, spec.default)
                if value not in _VALID_CALENDAR_TRANSPORTS:
                    errors.append(
                        f"{spec.yaml_path} must be one of "
                        f"{sorted(_VALID_CALENDAR_TRANSPORTS)!r}, got {value!r}"
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
        elif spec.kind == "log_level":
            if raw.upper() not in _VALID_LOG_LEVELS:
                errors.append(
                    f"{label} must be one of {sorted(_VALID_LOG_LEVELS)!r}, got {raw!r}"
                )
            kwargs[spec.field_name] = raw
        elif spec.kind == "log_format":
            if raw.lower() not in _VALID_LOG_FORMATS:
                errors.append(
                    f"{label} must be one of "
                    f"{sorted(_VALID_LOG_FORMATS)!r}, got {raw!r}"
                )
            kwargs[spec.field_name] = raw
        elif spec.kind == "calendar_transport":
            if raw not in _VALID_CALENDAR_TRANSPORTS:
                errors.append(
                    f"{label} must be one of "
                    f"{sorted(_VALID_CALENDAR_TRANSPORTS)!r}, got {raw!r}"
                )
            kwargs[spec.field_name] = raw
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
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        try:
            data = read_yaml_file(path)
        except YamlConfigError as exc:
            raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc

        accounts_raw = data.get("accounts") if isinstance(data, dict) else None
        if accounts_raw is None:
            # The single-account ("mono") YAML file shape is no longer
            # supported — reject it with an actionable error.
            raise ConfigurationError(_mono_shape_error(path))

        if not isinstance(accounts_raw, list):
            raise ConfigurationError("Config key 'accounts' must be a list")
        if not accounts_raw:
            raise ConfigurationError("'accounts' must contain at least one account")

        # -- top-level llm / langfuse sections (application-wide) -----------

        global_llm_api_key: str = ""
        global_llm_provider: str = ""
        global_langfuse_public_key: str = ""
        global_langfuse_secret_key: str = ""
        global_langfuse_base_url: str = ""

        # -- top-level board_agent section (application-wide) --------------

        global_board_agent_enabled: bool = False
        global_board_agent_api_url: str = ""
        global_board_agent_api_token: str = ""
        global_board_agent_repo_id: str = ""
        global_board_agent_write_ops: bool = True

        if isinstance(data, dict):
            llm_section = _get_table(data, "llm")
            if llm_section is not None:
                global_llm_api_key = _get_str(llm_section, "api_key", "")
                global_llm_provider = _get_str(llm_section, "provider", "")

            langfuse_section = _get_table(data, "langfuse")
            if langfuse_section is not None:
                global_langfuse_public_key = _get_str(
                    langfuse_section, "public_key", ""
                )
                global_langfuse_secret_key = _get_str(
                    langfuse_section, "secret_key", ""
                )
                global_langfuse_base_url = _get_str(langfuse_section, "base_url", "")

            board_agent_section = _get_table(data, "board_agent")
            if board_agent_section is not None:
                global_board_agent_enabled = _get_bool(
                    board_agent_section, "enabled", False
                )
                global_board_agent_api_url = _get_str(
                    board_agent_section, "api_url", ""
                )
                global_board_agent_api_token = _get_str(
                    board_agent_section, "api_token", ""
                )
                global_board_agent_repo_id = _get_str(
                    board_agent_section, "repo_id", ""
                )
                global_board_agent_write_ops = _get_bool(
                    board_agent_section, "write_ops", True
                )

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

            # llm: and langfuse: are now top-level (application-wide);
            # per-account blocks are rejected with an actionable error.
            for section_name in ("llm", "langfuse", "board_agent"):
                if section_name in entry:
                    raise ConfigurationError(
                        f"account {raw_id!r} has a per-account "
                        f"{section_name!r} block — {section_name}: is now "
                        f"a top-level section. Move it outside the "
                        f"accounts: list."
                    )

            store_section = entry.get("store")
            has_store_path = isinstance(store_section, dict) and "path" in store_section
            cfg = MailConfig._parse_config_dict(entry, path, validate=validate)
            if not has_store_path:
                cfg = dataclasses.replace(cfg, db_path=f".data/{raw_id}/mail.db")

            # Apply top-level llm / langfuse / board_agent values
            # (global wins over defaults).
            cfg = dataclasses.replace(
                cfg,
                llm_api_key=global_llm_api_key or cfg.llm_api_key,
                llm_provider=global_llm_provider or cfg.llm_provider,
                langfuse_public_key=global_langfuse_public_key
                or cfg.langfuse_public_key,
                langfuse_secret_key=global_langfuse_secret_key
                or cfg.langfuse_secret_key,
                langfuse_base_url=global_langfuse_base_url or cfg.langfuse_base_url,
                board_agent_enabled=global_board_agent_enabled,
                board_agent_api_url=global_board_agent_api_url
                or cfg.board_agent_api_url,
                board_agent_api_token=global_board_agent_api_token
                or cfg.board_agent_api_token,
                board_agent_repo_id=global_board_agent_repo_id
                or cfg.board_agent_repo_id,
                board_agent_write_ops=global_board_agent_write_ops,
            )

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
          ``MAIL_ACCOUNTS_<n>_<X>``.  ``LLM_API_KEY``, ``LLM_PROVIDER``,
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
        skip_global=True,
    )

    # Global fields are application-wide; read them from bare (non-namespaced)
    # env vars.  They were skipped in _build_config_from_env above.
    cfg = dataclasses.replace(
        cfg,
        llm_api_key=os.environ.get("LLM_API_KEY", cfg.llm_api_key),
        llm_provider=os.environ.get("LLM_PROVIDER", cfg.llm_provider),
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
        board_agent_enabled=_parse_bool(
            "BOARD_AGENT_ENABLED",
            os.environ.get("BOARD_AGENT_ENABLED", str(cfg.board_agent_enabled)),
        ),
        board_agent_api_url=os.environ.get(
            "BOARD_AGENT_API_URL", cfg.board_agent_api_url
        ),
        board_agent_api_token=os.environ.get(
            "BOARD_AGENT_API_TOKEN", cfg.board_agent_api_token
        ),
        board_agent_repo_id=os.environ.get(
            "BOARD_AGENT_REPO_ID", cfg.board_agent_repo_id
        ),
        board_agent_write_ops=_parse_bool(
            "BOARD_AGENT_WRITE_OPS",
            os.environ.get("BOARD_AGENT_WRITE_OPS", str(cfg.board_agent_write_ops)),
        ),
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
