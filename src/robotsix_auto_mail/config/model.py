"""Configuration model: the ``MailConfig`` / ``MailAccount`` pydantic models.

Holds the immutable configuration models and the multi-account container.
Depends on :mod:`robotsix_auto_mail.config.schema` for the error type and
validation constants.  The YAML loader (``from_yaml``) has been removed —
``robotsix_config.load_config`` replaces it for JSON config files.
"""

from __future__ import annotations

import logging
import re
from typing import Final, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from robotsix_auto_mail.config.credentials import (
    LangfuseConfig,
    OpenRouterConfig,
)
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
        # SecretStr fields: extract the raw value for template checking.
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
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
    """Immutable per-mailbox settings: mail server connection parameters.

    Sensitive fields (``password``, ``oauth2_token``,
    ``oauth2_client_secret``) are typed as :class:`pydantic.SecretStr` so
    the JSON schema emits ``writeOnly`` and the values are masked in
    ``repr`` / ``str``.

    LLM and Langfuse credentials are deliberately **not** here: they belong
    to the component, not to a mailbox, and live in the canonical
    ``langfuse`` / ``openrouter`` blocks on :class:`MailAccountsConfig`
    (see :mod:`robotsix_auto_mail.config.credentials`).
    """

    model_config = ConfigDict(frozen=True)

    imap_host: str = Field(
        description="IMAP server hostname, e.g. `imap.gmail.com`."
    )
    smtp_host: str = Field(
        description="SMTP server hostname, e.g. `smtp.gmail.com`."
    )
    username: str = Field(
        description="Mailbox username (usually the full email address)."
    )
    password: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Mailbox password or app-specific password. "
            "Leave empty when using OAuth2."
        ),
    )

    imap_port: int = Field(
        default=993,
        description="IMAP server port (993 for direct TLS, 143 for STARTTLS).",
        json_schema_extra={"advanced": True},
    )
    imap_tls_mode: str = Field(
        default=DEFAULT_IMAP_TLS_MODE,
        description=(
            "TLS negotiation mode for IMAP: `direct-tls` (connect then TLS, "
            "default), `starttls` (upgrade after connect), or `none`."
        ),
        json_schema_extra={"advanced": True},
    )
    smtp_port: int = Field(
        default=587,
        description="SMTP server port (587 for STARTTLS, 465 for direct TLS).",
        json_schema_extra={"advanced": True},
    )
    smtp_tls_mode: str = Field(
        default=DEFAULT_SMTP_TLS_MODE,
        description=(
            "TLS negotiation mode for SMTP: `direct-tls`, `starttls` "
            "(default), or `none`."
        ),
        json_schema_extra={"advanced": True},
    )

    # Empty by default; the accounts loader derives ``.data/<id>/mail.db``
    # per account when ``store.path`` is absent.
    db_path: str = Field(
        default="",
        description=(
            "Path to the per-account SQLite database file. "
            "Empty means derive `<data-dir>/<account_id>/mail.db`."
        ),
    )
    imap_folder: str = Field(
        default="INBOX",
        description="IMAP folder to monitor, e.g. `INBOX`.",
    )

    # Minutes between automatic ingest cycles (`ingest --watch`).
    ingest_interval_minutes: int = Field(
        default=DEFAULT_INGEST_INTERVAL_MINUTES,
        description=(
            "Minutes between automatic ingest cycles "
            "when `ingest_mode` is `watch`."
        ),
        json_schema_extra={"advanced": True},
    )

    # Ingest mode: ``"once"`` (single pass, the default) or ``"watch"``
    # (loop forever on an interval).  The entrypoint reads this field when
    # no CLI command is given and auto-starts the watch loop when set to
    # ``"watch"``.  The ``ingest`` CLI subcommand also merges this field
    # with its ``--watch`` flag.
    ingest_mode: Literal["watch", "once"] = Field(
        default="once",
        description=(
            "Ingest behaviour: `once` runs a single poll then exits, "
            "`watch` loops forever on `ingest_interval_minutes`."
        ),
        json_schema_extra={"advanced": True},
    )

    # Heartbeat file path — touched at the end of each poll cycle in
    # ``--watch`` mode so a Docker HEALTHCHECK can verify the loop is
    # alive.  An empty string means no file is written.
    heartbeat_file: str = Field(
        default="",
        description=(
            "File touched at the end of each poll cycle in watch mode "
            "for Docker HEALTHCHECK. Empty means no heartbeat."
        ),
        json_schema_extra={"advanced": True},
    )

    # Self-managed archive folder structure.
    archive_root: str = Field(
        default=DEFAULT_ARCHIVE_ROOT,
        description="Root directory for the self-managed archive folder structure.",
        json_schema_extra={"advanced": True},
    )
    archive_enabled: bool = Field(
        default=True,
        description=(
            "When true, processed messages are moved into "
            "the archive; when false they stay in the inbox."
        ),
        json_schema_extra={"advanced": True},
    )

    # Run the inbox triage agent automatically at the end of each ingest.
    triage_on_ingest: bool = Field(
        default=True,
        description=(
            "When true, the inbox triage agent runs "
            "automatically after each ingest cycle."
        ),
        json_schema_extra={"advanced": True},
    )

    # Path to the human-readable triage rules file maintained by the flash
    # LLM from user actions.  Empty means "derive from db_path"
    # (``<db-dir>/triage_rules.md``).
    triage_rules_path: str = Field(
        default="",
        description=(
            "Path to the human-readable triage rules file. "
            "Empty means derive from db_path."
        ),
        json_schema_extra={"advanced": True},
    )

    # OAuth2 / XOAUTH2 credentials (Gmail, Microsoft 365, etc.).
    # Optional; when ``oauth2_token`` is set, SASL XOAUTH2 is used
    # instead of password-based ``login()``.
    oauth2_token: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "Static OAuth2 access token for SASL XOAUTH2 authentication. "
            "Leave empty for password auth or MSAL-managed tokens."
        ),
    )
    oauth2_client_id: str = Field(
        default="",
        description=(
            "OAuth2 client id (application registration id). "
            "Only needed for MSAL-managed OAuth2."
        ),
    )
    oauth2_client_secret: SecretStr = Field(
        default=SecretStr(""),
        description="OAuth2 client secret. Only needed for MSAL-managed OAuth2.",
    )

    # MSAL-managed OAuth2 (Microsoft 365). When ``oauth2_provider`` is set
    # to ``"microsoft"``, access tokens are acquired and refreshed via MSAL
    # instead of password/static-token auth. ``oauth2_tenant`` is the Azure
    # AD tenant (default ``organizations``).
    oauth2_provider: str = Field(
        default="",
        description=(
            "OAuth2 provider identifier. Set to `microsoft` "
            "for MSAL-managed Microsoft 365 tokens; "
            "empty means disabled."
        ),
        json_schema_extra={"advanced": True},
    )
    oauth2_tenant: str = Field(
        default="organizations",
        description=(
            "Azure AD tenant for MSAL OAuth2, e.g. "
            "`organizations`, `common`, or a tenant id."
        ),
        json_schema_extra={"advanced": True},
    )

    # Logging configuration — application-wide (global).
    log_level: str = Field(
        default="INFO",
        description=(
            "Application log level: `DEBUG`, `INFO`, "
            "`WARNING`, `ERROR`, or `CRITICAL`."
        ),
    )
    log_format: str = Field(
        default="console",
        description=(
            "Log output format: `console` (human-readable) "
            "or `json` (structured)."
        ),
        json_schema_extra={"advanced": True},
    )

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
        "oauth2_token",
        "oauth2_client_secret",
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

    account_id: str = Field(
        description=(
            "Stable identifier for this mailbox, used in filenames and "
            "selectors. Must match `^[A-Za-z0-9._-]+$`."
        ),
    )
    config: MailConfig = Field(
        description="Per-mailbox connection and behaviour settings.",
    )
    label: str | None = Field(
        default=None,
        description="Optional human-friendly display name for this account.",
    )

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
# TierModelsConfig — per-level model overrides
# ---------------------------------------------------------------------------


class TierModelsConfig(BaseModel):
    """Per-level model overrides for the four LLM tiers.

    Each field holds a provider-model identifier
    (e.g. ``"openrouter[deepseek]-deepseek/deepseek-v4-flash"``).
    Empty means use the llmio tier default for that level.
    """

    model_config = ConfigDict(frozen=True)

    level1: str = Field(
        default="",
        description=(
            "Provider-model identifier for tier 1 (cheapest), "
            "e.g. `openrouter[deepseek]-deepseek/deepseek-chat`. "
            "Empty uses the llmio tier default."
        ),
    )
    level2: str = Field(
        default="",
        description=(
            "Provider-model identifier for tier 2. "
            "Empty uses the llmio tier default."
        ),
    )
    level3: str = Field(
        default="",
        description=(
            "Provider-model identifier for tier 3. "
            "Empty uses the llmio tier default."
        ),
    )
    level4: str = Field(
        default="",
        description=(
            "Provider-model identifier for tier 4 (most capable). "
            "Empty uses the llmio tier default."
        ),
    )


# ---------------------------------------------------------------------------
# MailAccountsConfig
# ---------------------------------------------------------------------------


class MailAccountsConfig(BaseModel):
    """An ordered collection of :class:`MailAccount`s.

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

    Component-wide settings
    -----------------------
    The ``langfuse``, ``openrouter``, ``models``, and per-application level
    fields sit here rather than on each :class:`MailConfig` because they
    describe the component's one LLM function, not a mailbox.  Their shape
    is fixed by robotsix-standards — see
    :mod:`robotsix_auto_mail.config.credentials`.

    Validation (all raise :class:`ConfigurationError`): all ``account_id``s
    unique; all ``MailConfig.db_path``s unique across accounts.
    """

    model_config = ConfigDict(frozen=True)

    accounts: list[MailAccount] = Field(
        description=(
            "Ordered list of mailbox accounts. Each entry "
            "has an account_id and its MailConfig."
        ),
    )

    #: The canonical credential blocks, shared by every account.
    langfuse: LangfuseConfig = Field(
        default_factory=LangfuseConfig,
        description=(
            "Langfuse tracing configuration: instance host "
            "and per-project credentials."
        ),
    )
    openrouter: OpenRouterConfig = Field(
        default_factory=OpenRouterConfig,
        description=(
            "OpenRouter provider keys, keyed by the same "
            "aliases as langfuse.projects."
        ),
    )

    #: Per-level model overrides.  Each field holds a provider-model
    #: identifier (e.g. ``"openrouter[deepseek]-deepseek/deepseek-v4-flash"``).
    #: Empty means use the llmio tier default for that level.
    models: TierModelsConfig = Field(
        default_factory=TierModelsConfig,
        description=(
            "Per-tier model overrides. Each field holds a provider-model "
            "identifier; empty uses the llmio tier default."
        ),
    )

    #: Tier level assigned to each application / task.  Different tasks
    #: can use different tiers (e.g. triage=1 cheap, draft=3 high).
    triage_level: int = Field(
        default=1,
        description=(
            "Tier level (1-4) assigned to the inbox triage agent. "
            "Higher tiers use more capable (and expensive) models."
        ),
    )
    classifier_level: int = Field(
        default=1,
        description="Tier level (1-4) assigned to the message classifier agent.",
    )
    rules_level: int = Field(
        default=1,
        description="Tier level (1-4) assigned to the triage-rules synthesis agent.",
    )
    detector_level: int = Field(
        default=1,
        description="Tier level (1-4) assigned to the provider-detection agent.",
    )
    draft_level: int = Field(
        default=1,
        description="Tier level (1-4) assigned to the draft-reply agent.",
    )

    @model_validator(mode="after")
    def _validate(self) -> MailAccountsConfig:
        ids = [a.account_id for a in self.accounts]
        if len(ids) != len(set(ids)):
            raise ConfigurationError("duplicate account_id values")
        paths = [a.config.db_path for a in self.accounts if a.config.db_path]
        if len(paths) != len(set(paths)):
            raise ConfigurationError("duplicate db_path values")
        return self

    def with_accounts(self, accounts: list[MailAccount]) -> MailAccountsConfig:
        """Return a copy with a new account list, keeping everything else.

        Every flow that adds, removes or re-detects an account has to go
        through this rather than constructing a fresh container: a bare
        ``MailAccountsConfig(accounts=…)`` silently
        resets the component-wide blocks, which would wipe the operator's
        Langfuse and OpenRouter credentials on the next account edit.

        Validation runs exactly as it does on construction, so callers keep
        catching the same errors for a duplicate id.
        """
        # Shallow field mapping, so any field added later is carried over
        # without another edit here.
        data = dict(self)
        data["accounts"] = accounts
        return type(self).model_validate(data)

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
