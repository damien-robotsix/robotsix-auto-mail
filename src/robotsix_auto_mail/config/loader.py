"""Configuration loaders: the public ``load*`` entry points.

Wires the schema and model layers into the cascade that resolves the
effective configuration — see :func:`load`: code defaults → YAML file →
environment variables (which win field-by-field).  Depends on
:mod:`robotsix_auto_mail.config.schema` and
:mod:`robotsix_auto_mail.config.model`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from robotsix_yaml_config import (  # type: ignore[import-untyped]
    YamlConfigError,
    read_yaml_file,
)

from robotsix_auto_mail.config.model import MailAccountsConfig, MailConfig
from robotsix_auto_mail.config.schema import ConfigurationError, _mono_shape_error

logger = logging.getLogger(__name__)

# Default YAML config file path (used by ``load()`` and ``load_llm()``).
DEFAULT_CONFIG_PATH = "config/mail.local.yaml"


# ---------------------------------------------------------------------------
# Convenience loader
# ---------------------------------------------------------------------------


def load() -> MailConfig:
    """Load the **default account's** :class:`MailConfig`.

    Delegates to :func:`load_accounts` and returns the default account's
    config.  This works against the multi-account shape (the default, or
    first, account is used) and a single-account ``MAIL_*`` environment.

    Kept as a thin convenience for the best-effort Langfuse tracing init in
    ``cli.main()`` and for ``load_llm``-style callers that only need one
    representative account's settings.
    """
    return load_accounts().default.config


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
                # single-account ("mono") file raises ``ConfigurationError``
                # (caught below) — the LLM key then degrades to env-only.
                accounts = MailAccountsConfig.from_yaml(config_path, validate=False)
                file_cfg: MailConfig | None = accounts.default.config
            except (ConfigurationError, FileNotFoundError, OSError):
                file_cfg = None
            if file_cfg is not None:
                api_key = api_key or file_cfg.llm_api_key

    return api_key


def load_llm_provider() -> str:
    """Resolve the LLM provider through the same cascade as :func:`load_llm`.

    Order: ``LLM_PROVIDER`` environment variable wins; otherwise the
    ``llm.provider`` field of the YAML config file at ``MAIL_CONFIG_PATH``
    (default ``config/mail.local.yaml``) is consulted; falls back to
    ``"openrouter-deepseek"``.
    """
    provider = os.environ.get("LLM_PROVIDER", "")

    if not provider:
        config_path = Path(os.environ.get("MAIL_CONFIG_PATH", DEFAULT_CONFIG_PATH))
        if config_path.exists():
            try:
                accounts = MailAccountsConfig.from_yaml(config_path, validate=False)
                file_cfg: MailConfig | None = accounts.default.config
            except (ConfigurationError, FileNotFoundError, OSError):
                file_cfg = None
            if file_cfg is not None:
                provider = provider or file_cfg.llm_provider

    return provider or "openrouter-deepseek"


def load_accounts() -> MailAccountsConfig:
    """Load ``MailAccountsConfig`` through the same cascade as :func:`load`.

    1.  Call :meth:`MailAccountsConfig.from_env`.  If the environment fully
        describes the accounts (namespaced multi-account, or a complete
        single-account env), return immediately — env wins.
    2.  Otherwise, if *only* required fields are missing (no invalid values),
        fall back to the YAML config file at ``MAIL_CONFIG_PATH`` (default
        ``config/mail.local.yaml``).  A multi-account file is parsed directly;
        a single-account ("mono") file is no longer supported and raises an
        actionable :class:`ConfigurationError` naming ``migrate-config`` and
        ``detect``.

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

    if config_path.exists():
        # The single-account ("mono") YAML file shape is no longer supported.
        raise ConfigurationError(_mono_shape_error(config_path))

    # No usable env and no config file — surface the env's missing-field error.
    return MailAccountsConfig.from_env()
