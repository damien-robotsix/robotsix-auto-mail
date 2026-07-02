"""Configuration model: the ``MailConfig`` / ``MailAccount`` pydantic models.

Holds the immutable configuration models and the multi-account YAML
loader (``MailAccountsConfig.from_yaml``) plus the per-account section-parsing
helpers that construct them.  Depends on
:mod:`robotsix_auto_mail.config.schema` for the error type and validation
constants.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from robotsix_yaml_config import (
    YamlConfigError,
    read_yaml_file,
)

from robotsix_auto_mail.config.schema import (
    DEFAULT_ARCHIVE_ROOT,
    DEFAULT_IMAP_TLS_MODE,
    DEFAULT_INGEST_INTERVAL_MINUTES,
    DEFAULT_SMTP_TLS_MODE,
    ConfigurationError,
    _mono_shape_error,
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
        if field_name == "password":
            value = cfg.password.get_secret_value()
        if value and _TEMPLATE_LITERAL_RE.search(value):
            display = "<redacted>" if field_name == "password" else repr(value)
            raise ConfigurationError(
                f"Config field '{field_name}' contains an unsubstituted "
                f"template literal: {display}. "
                f"Check your config rendering pipeline."
            )


# ---------------------------------------------------------------------------
# Failed-account tracking — records an account that failed validation at
# config-load time so the board can degrade gracefully.
# ---------------------------------------------------------------------------


class FailedAccountEntry(BaseModel):
    """Records an account that failed validation at config-load time."""

    model_config = ConfigDict(frozen=True)

    account_id: str  # raw ``id:`` field from YAML / env, or ``'<account-N>'`` fallback
    error: str  # the ConfigurationError message


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


class MailConfig(BaseModel):
    """Immutable application settings: mail server connection parameters
    plus optional LLM credentials used by ``detect`` (and future mail
    processing).

    Credentials are stored as ``SecretStr`` values — masked in ``repr`` / ``str``.
    """

    model_config = ConfigDict(frozen=True)

    imap_host: str
    smtp_host: str
    username: str
    password: SecretStr = Field(default=SecretStr(""))

    imap_port: int = 993
    imap_tls_mode: Literal["starttls", "direct-tls", "none"] = DEFAULT_IMAP_TLS_MODE
    smtp_port: int = 587
    smtp_tls_mode: Literal["starttls", "direct-tls", "none"] = DEFAULT_SMTP_TLS_MODE

    # Empty by default; the accounts loader derives ``.data/<id>/mail.db``
    # per account when ``store.path`` is absent.
    db_path: str = ""
    imap_folder: str = "INBOX"

    # LLM provider settings — optional; only needed for the `detect`
    # subcommand and future LLM-assisted mail processing.
    llm_api_key: SecretStr = Field(default=SecretStr(""))
    llm_provider_model: str = ""

    # Minutes between automatic ingest cycles (`ingest --watch`).
    ingest_interval_minutes: int = DEFAULT_INGEST_INTERVAL_MINUTES

    # Self-managed archive folder structure.
    archive_root: str = DEFAULT_ARCHIVE_ROOT
    archive_enabled: bool = True

    # Run the inbox triage agent automatically at the end of each ingest.
    triage_on_ingest: bool = True

    # Path to the human-readable triage rules file maintained by the flash
    # LLM from user actions.  Empty means "derive from db_path"
    # (``<db-dir>/triage_rules.md``).
    triage_rules_path: str = ""

    # Whether the component-agent HTTP API (monitor / config-get / config-set)
    # is served on the board server.
    component_agent_enabled: bool = False

    # OAuth2 / XOAUTH2 credentials (Gmail, Microsoft 365, etc.).
    # Optional; when ``oauth2_token`` is set, SASL XOAUTH2 is used
    # instead of password-based ``login()``.
    oauth2_token: SecretStr = Field(default=SecretStr(""))
    oauth2_client_id: str = ""
    oauth2_client_secret: SecretStr = Field(default=SecretStr(""))

    # MSAL-managed OAuth2 (Microsoft 365). When ``oauth2_provider`` is set
    # to ``"microsoft"``, access tokens are acquired and refreshed via MSAL
    # instead of password/static-token auth. ``oauth2_tenant`` is the Azure
    # AD tenant (default ``organizations``).
    oauth2_provider: str = ""
    oauth2_tenant: str = "organizations"

    # Langfuse observability — optional; when public_key/secret_key are set,
    # every LLM agent run is traced to the configured Langfuse project.
    langfuse_public_key: str = ""
    langfuse_secret_key: SecretStr = Field(default=SecretStr(""))
    langfuse_base_url: str = ""

    # Logging configuration — application-wide (global).
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "console"
    log_file_dir: str = ".mail_log"

    # -- validators --------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _flatten_yaml_sections(cls, data: Any) -> Any:
        """Map nested per-account YAML sections to flat ``MailConfig`` field names.

        This is a ``mode='before'`` validator so it runs before pydantic's
        field-level validation and coercion.  When *data* is not a dict
        (already a ``MailConfig`` instance or fields dict), it passes through.
        """
        if not isinstance(data, dict):
            return data

        # Mapping: dotted YAML key → MailConfig field name
        mapping = {
            "imap.host": "imap_host",
            "imap.port": "imap_port",
            "imap.tls_mode": "imap_tls_mode",
            "imap.folder": "imap_folder",
            "smtp.host": "smtp_host",
            "smtp.port": "smtp_port",
            "smtp.tls_mode": "smtp_tls_mode",
            "auth.username": "username",
            # pragma: allowlist nextline secret
            "auth.password": "password",
            "auth.oauth2_provider": "oauth2_provider",
            "auth.oauth2_tenant": "oauth2_tenant",
            "auth.oauth2_token": "oauth2_token",
            "auth.oauth2_client_id": "oauth2_client_id",
            # pragma: allowlist nextline secret
            "auth.oauth2_client_secret": "oauth2_client_secret",
            "store.path": "db_path",
            "archive.root": "archive_root",
            "archive.enabled": "archive_enabled",
            "triage.on_ingest": "triage_on_ingest",
            "triage.rules_path": "triage_rules_path",
            "ingest.interval_minutes": "ingest_interval_minutes",
            "component_agent.enabled": "component_agent_enabled",
        }

        result: dict[str, Any] = {}
        for yaml_key, field_name in mapping.items():
            section_name, key_name = yaml_key.split(".", 1)
            section = data.get(section_name)
            if isinstance(section, dict) and key_name in section:
                result[field_name] = section[key_name]

        # Also pass through any already-flat keys (e.g. from direct construction)
        for key, value in data.items():
            if key not in result and "." not in key:
                result[key] = value

        return result


# ---------------------------------------------------------------------------
# Multi-account model
# ---------------------------------------------------------------------------

# Per-account stable identifier charset.  It is used in SQLite filenames and
# (later in the epic) in URLs / board selectors, so keep it filesystem- and
# URL-safe.
_ACCOUNT_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]+$")


class MailAccount(BaseModel):
    """One named mailbox: a stable ``account_id`` plus its ``MailConfig``.

    ``label`` is an optional human-friendly display name.  ``account_id`` is
    a stable identifier (e.g. ``"personal"``) used in the account's SQLite
    filename and, later in the epic, in URLs / board selectors — so it must
    be non-empty and match ``^[A-Za-z0-9._-]+$``.
    """

    model_config = ConfigDict(frozen=True)

    account_id: str
    config: MailConfig
    label: str | None = None

    @field_validator("account_id", mode="after")
    @classmethod
    def _validate_account_id(cls, v: str) -> str:
        if not v:
            raise ConfigurationError("account_id must be non-empty")
        if not _ACCOUNT_ID_RE.match(v):
            raise ConfigurationError(
                f"account_id {v!r} must match {_ACCOUNT_ID_RE.pattern!r}"
            )
        return v


class MailAccountsConfig(BaseModel):
    """An ordered collection of :class:`MailAccount`s plus a default id.

    One SQLite DB per account
    -------------------------
    Multiple accounts are modelled as N independent :class:`MailConfig`
    instances, each carrying its **own** ``db_path``, rather than adding an
    ``account_id`` column to every table.  The rationale:

    - Per-account state (triage decisions, archive watermarks — all keyed
      by ``message_id`` in each DB, plus the per-account ``triage_rules.md``
      file) is naturally isolated with zero schema migration.
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

    model_config = ConfigDict(frozen=True)

    accounts: list[MailAccount]
    default_account_id: str
    failed_accounts: tuple[FailedAccountEntry, ...] = Field(default=())

    @model_validator(mode="after")
    def _validate_accounts(self) -> MailAccountsConfig:
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

        return self

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
          :class:`MailConfig`'s ``_flatten_yaml_sections`` validator.
          ``llm:`` and ``langfuse:`` are **top-level** sections
          (application-wide); they are applied to every account via
          :meth:`MailConfig.model_copy`.  An optional top-level
          ``default_account:`` names the default (absent → the first entry).
          When an entry omits ``store.path`` the per-account default
          ``".data/<id>/mail.db"`` is used so DBs never collide.
        * **Legacy single-account** — no top-level ``accounts:`` key.  The
          whole file is rejected with an actionable error.

        ``validate`` is accepted for API compatibility but is a no-op —
        pydantic always validates.

        Raises:
            ConfigurationError: On invalid structure or failed validation.
            FileNotFoundError: If *path* does not exist.
        """
        _ = validate  # accepted for API compat; pydantic always validates
        _ = validate  # accepted for API compat; pydantic always validates
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
        global_log_level: str = ""
        global_log_format: str = ""
        global_log_file_dir: str = ""

        if isinstance(data, dict):
            llm = data.get("llm")
            if isinstance(llm, dict):
                global_llm_api_key = str(llm.get("api_key", ""))
                global_llm_provider_model = str(llm.get("provider_model", ""))

            langfuse = data.get("langfuse")
            if isinstance(langfuse, dict):
                global_langfuse_public_key = str(langfuse.get("public_key", ""))
                global_langfuse_secret_key = str(langfuse.get("secret_key", ""))
                global_langfuse_base_url = str(langfuse.get("base_url", ""))

            logging_section = data.get("logging")
            if isinstance(logging_section, dict):
                global_log_level = str(logging_section.get("level", ""))
                global_log_format = str(logging_section.get("format", ""))
                global_log_file_dir = str(logging_section.get("file_dir", ""))

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
                cfg = MailConfig.model_validate(entry)
                _validate_template_literals(cfg)
                if not has_store_path:
                    cfg = cfg.model_copy(update={"db_path": f".data/{raw_id}/mail.db"})

                # Apply top-level llm / langfuse values
                # (global wins over defaults).
                cfg = cfg.model_copy(
                    update={
                        "llm_api_key": SecretStr(
                            global_llm_api_key or cfg.llm_api_key.get_secret_value()
                        ),
                        "llm_provider_model": global_llm_provider_model
                        or cfg.llm_provider_model,
                        "langfuse_public_key": global_langfuse_public_key
                        or cfg.langfuse_public_key,
                        "langfuse_secret_key": SecretStr(
                            global_langfuse_secret_key
                            or cfg.langfuse_secret_key.get_secret_value()
                        ),
                        "langfuse_base_url": global_langfuse_base_url
                        or cfg.langfuse_base_url,
                        "log_level": global_log_level or cfg.log_level,
                        "log_format": global_log_format or cfg.log_format,
                        "log_file_dir": global_log_file_dir or cfg.log_file_dir,
                    }
                )

                accounts.append(
                    MailAccount(account_id=raw_id, config=cfg, label=raw_label)
                )
            except (ConfigurationError, ValidationError) as exc:
                if isinstance(exc, ValidationError):
                    exc = ConfigurationError(str(exc))
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
            accounts=accounts,
            default_account_id=default_id,
            failed_accounts=tuple(failed),
        )
