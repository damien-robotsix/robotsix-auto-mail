"""Configuration model: the ``MailConfig`` / ``MailAccount`` pydantic models.

Holds the immutable configuration models and the multi-account container.
Depends on :mod:`robotsix_auto_mail.config.schema` for the error type and
validation constants.  The YAML loader (``from_yaml``) has been removed —
``robotsix_config.load_config`` replaces it for JSON config files.
"""

from __future__ import annotations

import logging
import re
from typing import Final

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from robotsix_auto_mail.config.schema import (
    _VALID_LOG_FORMATS,
    _VALID_LOG_LEVELS,
    _VALID_TLS_MODES,
    DEFAULT_ARCHIVE_ROOT,
    DEFAULT_IMAP_TLS_MODE,
    DEFAULT_INGEST_INTERVAL_MINUTES,
    DEFAULT_SMTP_TLS_MODE,
    ConfigurationError,
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
            display = "<redacted>" if field_name == "password" else repr(value)
            raise ConfigurationError(
                f"Config field '{field_name}' contains an unsubstituted "
                f"template literal: {display}. "
                f"Check your config rendering pipeline."
            )


# ---------------------------------------------------------------------------
# MailConfig
# ---------------------------------------------------------------------------


class MailConfig(BaseModel):
    """Immutable application settings: mail server connection parameters
    plus optional LLM credentials used by ``detect`` (and future mail
    processing).

    Credentials are stored in memory as plain ``str`` values but the
    ``password``, ``llm_api_key``, ``oauth2_token``, ``oauth2_client_secret``,
    and ``langfuse_secret_key`` fields are masked in ``repr`` / ``str``.
    """

    model_config = ConfigDict(frozen=True)

    imap_host: str
    smtp_host: str
    username: str
    password: str

    imap_port: int = 993
    imap_tls_mode: str = DEFAULT_IMAP_TLS_MODE
    smtp_port: int = 587
    smtp_tls_mode: str = DEFAULT_SMTP_TLS_MODE

    # Empty by default; the accounts loader derives ``.data/<id>/mail.db``
    # per account when ``store.path`` is absent.
    db_path: str = ""
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

    # Path to the human-readable triage rules file maintained by the flash
    # LLM from user actions.  Empty means "derive from db_path"
    # (``<db-dir>/triage_rules.md``).
    triage_rules_path: str = ""

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

    # -- validators --------------------------------------------------------

    @field_validator("imap_tls_mode")
    @classmethod
    def _validate_imap_tls_mode(cls, v: str) -> str:
        if v not in _VALID_TLS_MODES:
            raise ValueError(
                f"imap_tls_mode must be one of {sorted(_VALID_TLS_MODES)!r}, got {v!r}"
            )
        return v

    @field_validator("smtp_tls_mode")
    @classmethod
    def _validate_smtp_tls_mode(cls, v: str) -> str:
        if v not in _VALID_TLS_MODES:
            raise ValueError(
                f"smtp_tls_mode must be one of {sorted(_VALID_TLS_MODES)!r}, got {v!r}"
            )
        return v

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        if v.upper() not in _VALID_LOG_LEVELS:
            raise ValueError(
                f"log_level must be one of {sorted(_VALID_LOG_LEVELS)!r}, got {v!r}"
            )
        return v.upper()

    @field_validator("log_format")
    @classmethod
    def _validate_log_format(cls, v: str) -> str:
        if v.lower() not in _VALID_LOG_FORMATS:
            raise ValueError(
                f"log_format must be one of {sorted(_VALID_LOG_FORMATS)!r}, got {v!r}"
            )
        return v.lower()

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
        parts: list[str] = []
        for field_name in type(self).model_fields:
            val = getattr(self, field_name)
            if field_name in self._SECRET_FIELDS:
                parts.append(f"{field_name}=<redacted>")
            else:
                parts.append(f"{field_name}={val!r}")
        return f"{cls}({', '.join(parts)})"

    def __str__(self) -> str:
        return self.__repr__()


# ---------------------------------------------------------------------------
# Per-account stable identifier charset
# ---------------------------------------------------------------------------

_ACCOUNT_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]+$")


# ---------------------------------------------------------------------------
# MailAccount
# ---------------------------------------------------------------------------


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

    @field_validator("account_id")
    @classmethod
    def _validate_account_id(cls, v: str) -> str:
        if not v:
            raise ConfigurationError("account_id must be non-empty")
        if not _ACCOUNT_ID_RE.match(v):
            raise ConfigurationError(
                f"account_id {v!r} must match {_ACCOUNT_ID_RE.pattern!r}"
            )
        return v


# ---------------------------------------------------------------------------
# MailAccountsConfig
# ---------------------------------------------------------------------------


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
      across accounts is therefore enforced at load time.

    Validation (all raise :class:`ConfigurationError`): at least one
    account; all ``account_id``s unique; all ``MailConfig.db_path``s unique
    across accounts; ``default_account_id`` resolves to a known account.
    """

    model_config = ConfigDict(frozen=True)

    accounts: list[MailAccount]
    default_account_id: str

    @model_validator(mode="after")
    def _validate(self) -> MailAccountsConfig:
        if not self.accounts:
            raise ConfigurationError("accounts list must not be empty")
        ids = [a.account_id for a in self.accounts]
        if len(ids) != len(set(ids)):
            raise ConfigurationError("duplicate account_id values")
        paths = [a.config.db_path for a in self.accounts if a.config.db_path]
        if len(paths) != len(set(paths)):
            raise ConfigurationError("duplicate db_path values")
        if self.default_account_id not in ids:
            raise ConfigurationError(
                f"default_account_id {self.default_account_id!r} not in accounts"
            )
        return self

    @property
    def default(self) -> MailAccount:
        """Return the :class:`MailAccount` for ``default_account_id``."""
        return next(a for a in self.accounts if a.account_id == self.default_account_id)

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

    def ids(self) -> tuple[str, ...]:
        """Return the ordered tuple of account ids."""
        return tuple(account.account_id for account in self.accounts)
