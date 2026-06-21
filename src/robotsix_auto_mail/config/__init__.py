"""Mail configuration subsystem.

Provides ``MailConfig``, a frozen dataclass that holds IMAP and SMTP
connection parameters, with two loaders: ``from_env()`` (environment
variables) and ``from_yaml()`` (a YAML file).

Configuration resolves through a single, predictable cascade — see
``load()``: code defaults → YAML file → environment variables (which
win field-by-field).

The implementation is split across internal submodules:

- ``schema`` — the ``ConfigurationError`` type, validation constants and
  defaults, the boolean parser, the ``_FIELD_SPECS`` table and the generic
  dict-extraction helpers.
- ``model`` — the ``MailConfig`` / ``MailAccount`` / ``MailAccountsConfig``
  dataclasses, their loaders, and the per-field environment build helpers.
- ``loader`` — the public ``load`` / ``load_llm`` / ``load_llm_provider_model`` /
  ``load_accounts`` cascade entry points.
- ``render`` — the multi-account YAML rendering helpers.

This module re-exports the public and previously-importable symbols so
``from robotsix_auto_mail.config import ...`` keeps working unchanged.
"""

from __future__ import annotations

from robotsix_auto_mail.config.loader import (
    DEFAULT_CONFIG_PATH as DEFAULT_CONFIG_PATH,
)
from robotsix_auto_mail.config.loader import (
    load as load,
)
from robotsix_auto_mail.config.loader import (
    load_accounts as load_accounts,
)
from robotsix_auto_mail.config.loader import (
    load_llm as load_llm,
)
from robotsix_auto_mail.config.loader import (
    load_llm_provider_model as load_llm_provider_model,
)
from robotsix_auto_mail.config.loader import (
    logger as logger,
)
from robotsix_auto_mail.config.loader import (
    resolve_llm_api_key as resolve_llm_api_key,
)
from robotsix_auto_mail.config.loader import (
    resolve_llm_provider_model as resolve_llm_provider_model,
)
from robotsix_auto_mail.config.model import (
    _ACCOUNT_ID_RE as _ACCOUNT_ID_RE,
)
from robotsix_auto_mail.config.model import (
    _ENV_ACCOUNT_INDEX_RE as _ENV_ACCOUNT_INDEX_RE,
)
from robotsix_auto_mail.config.model import (
    _ENV_ACCOUNTS_PREFIX as _ENV_ACCOUNTS_PREFIX,
)
from robotsix_auto_mail.config.model import (
    MailAccount as MailAccount,
)
from robotsix_auto_mail.config.model import (
    MailAccountsConfig as MailAccountsConfig,
)
from robotsix_auto_mail.config.model import (
    MailConfig as MailConfig,
)
from robotsix_auto_mail.config.model import (
    _build_account_from_env as _build_account_from_env,
)
from robotsix_auto_mail.config.model import (
    _build_config_from_env as _build_config_from_env,
)
from robotsix_auto_mail.config.render import (
    _render_account_block as _render_account_block,
)
from robotsix_auto_mail.config.render import (
    _yaml_scalar as _yaml_scalar,
)
from robotsix_auto_mail.config.render import (
    render_accounts_yaml as render_accounts_yaml,
)
from robotsix_auto_mail.config.schema import (
    _BOOL_FALSE as _BOOL_FALSE,
)
from robotsix_auto_mail.config.schema import (
    _BOOL_TRUE as _BOOL_TRUE,
)
from robotsix_auto_mail.config.schema import (
    _FIELD_SPECS as _FIELD_SPECS,
)
from robotsix_auto_mail.config.schema import (
    _REQUIRED as _REQUIRED,
)
from robotsix_auto_mail.config.schema import (
    _VALID_CALENDAR_TRANSPORTS as _VALID_CALENDAR_TRANSPORTS,
)
from robotsix_auto_mail.config.schema import (
    _VALID_LOG_FORMATS as _VALID_LOG_FORMATS,
)
from robotsix_auto_mail.config.schema import (
    _VALID_LOG_LEVELS as _VALID_LOG_LEVELS,
)
from robotsix_auto_mail.config.schema import (
    _VALID_TLS_MODES as _VALID_TLS_MODES,
)
from robotsix_auto_mail.config.schema import (
    DEFAULT_ARCHIVE_ROOT as DEFAULT_ARCHIVE_ROOT,
)
from robotsix_auto_mail.config.schema import (
    DEFAULT_DB_PATH as DEFAULT_DB_PATH,
)
from robotsix_auto_mail.config.schema import (
    DEFAULT_IMAP_TLS_MODE as DEFAULT_IMAP_TLS_MODE,
)
from robotsix_auto_mail.config.schema import (
    DEFAULT_INGEST_INTERVAL_MINUTES as DEFAULT_INGEST_INTERVAL_MINUTES,
)
from robotsix_auto_mail.config.schema import (
    DEFAULT_SMTP_TLS_MODE as DEFAULT_SMTP_TLS_MODE,
)
from robotsix_auto_mail.config.schema import (
    ConfigurationError as ConfigurationError,
)
from robotsix_auto_mail.config.schema import (
    _FieldSpec as _FieldSpec,
)
from robotsix_auto_mail.config.schema import (
    _get_bool as _get_bool,
)
from robotsix_auto_mail.config.schema import (
    _get_int as _get_int,
)
from robotsix_auto_mail.config.schema import (
    _get_str as _get_str,
)
from robotsix_auto_mail.config.schema import (
    _get_table as _get_table,
)
from robotsix_auto_mail.config.schema import (
    _mono_shape_error as _mono_shape_error,
)
from robotsix_auto_mail.config.schema import (
    _parse_bool as _parse_bool,
)

__all__ = [
    "DEFAULT_ARCHIVE_ROOT",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DB_PATH",
    "DEFAULT_IMAP_TLS_MODE",
    "DEFAULT_INGEST_INTERVAL_MINUTES",
    "DEFAULT_SMTP_TLS_MODE",
    "_ACCOUNT_ID_RE",
    "_BOOL_FALSE",
    "_BOOL_TRUE",
    "_ENV_ACCOUNTS_PREFIX",
    "_ENV_ACCOUNT_INDEX_RE",
    "_FIELD_SPECS",
    "_REQUIRED",
    "_VALID_CALENDAR_TRANSPORTS",
    "_VALID_LOG_FORMATS",
    "_VALID_LOG_LEVELS",
    "_VALID_TLS_MODES",
    "ConfigurationError",
    "MailAccount",
    "MailAccountsConfig",
    "MailConfig",
    "_FieldSpec",
    "_build_account_from_env",
    "_build_config_from_env",
    "_get_bool",
    "_get_int",
    "_get_str",
    "_get_table",
    "_mono_shape_error",
    "_parse_bool",
    "_render_account_block",
    "_yaml_scalar",
    "load",
    "load_accounts",
    "load_llm",
    "load_llm_provider_model",
    "logger",
    "render_accounts_yaml",
    "resolve_llm_api_key",
    "resolve_llm_provider_model",
]
