"""Mail configuration subsystem.

Provides ``MailConfig``, a pydantic model that holds IMAP and SMTP
connection parameters, loaded from ``config/config.json`` via the
``robotsix-config`` library.

Configuration resolves through a single, predictable cascade — see
``load()``: code defaults → JSON config file.

The implementation is split across internal submodules:

- ``schema`` — the ``ConfigurationError`` type, validation constants and
  defaults.
- ``model`` — the ``MailConfig`` / ``MailAccount`` / ``MailAccountsConfig``
  pydantic models.
- ``loader`` — the public ``load`` / ``load_accounts`` / ``get_config_schema``
  entry points.

This module re-exports the public and previously-importable symbols so
``from robotsix_auto_mail.config import ...`` keeps working unchanged.
"""

from __future__ import annotations

from robotsix_auto_mail.config.loader import (
    get_config_schema as get_config_schema,
)
from robotsix_auto_mail.config.loader import (
    load as load,
)
from robotsix_auto_mail.config.loader import (
    load_accounts as load_accounts,
)
from robotsix_auto_mail.config.loader import (
    resolve_llm_api_key as resolve_llm_api_key,
)
from robotsix_auto_mail.config.loader import (
    resolve_llm_provider_model as resolve_llm_provider_model,
)
from robotsix_auto_mail.config.loader import (
    save_accounts as save_accounts,
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
from robotsix_auto_mail.config.schema import (
    _VALID_TLS_MODES as _VALID_TLS_MODES,
)
from robotsix_auto_mail.config.schema import (
    DEFAULT_ARCHIVE_ROOT as DEFAULT_ARCHIVE_ROOT,
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

__all__ = [
    "DEFAULT_ARCHIVE_ROOT",
    "DEFAULT_IMAP_TLS_MODE",
    "DEFAULT_INGEST_INTERVAL_MINUTES",
    "DEFAULT_SMTP_TLS_MODE",
    "_VALID_TLS_MODES",
    "ConfigurationError",
    "MailAccount",
    "MailAccountsConfig",
    "MailConfig",
    "get_config_schema",
    "load",
    "load_accounts",
    "resolve_llm_api_key",
    "resolve_llm_provider_model",
    "save_accounts",
]
