"""Configuration loaders: the public ``load*`` entry points.

The single configuration source is the JSON file at ``ROBOTSIX_CONFIG_FILE``
(default ``config/config.json``), which must use the ``accounts:`` shape.
Configuration is loaded exclusively through :mod:`robotsix_config` — there
is no fallback or alternative config path.

The two LLM-only resolvers (:func:`resolve_llm_api_key`,
:func:`resolve_llm_provider_model`) check, in order: an explicit
argument, then the ``LLM_API_KEY`` / ``LLM_PROVIDER_MODEL``
environment variable, then the config file.

Depends on :mod:`robotsix_auto_mail.config.model`.
"""

from __future__ import annotations

import logging
import os

from robotsix_config import (
    config_schema_json as _config_schema_json,
)
from robotsix_config import (
    dump_config as _dump_config,
)
from robotsix_config import (
    load_config as _load_config,
)

from robotsix_auto_mail.config.model import MailAccountsConfig, MailConfig
from robotsix_auto_mail.config.schema import ConfigurationError

logger = logging.getLogger(__name__)


def load_accounts() -> MailAccountsConfig:
    """Load :class:`MailAccountsConfig` from ``config/config.json``
    (``ROBOTSIX_CONFIG_FILE``)."""
    return _load_config(MailAccountsConfig)


def load() -> MailConfig:
    """Return the **default account's** :class:`MailConfig` from the config file.

    A thin convenience for callers that only need one representative account's
    settings (e.g. the best-effort Langfuse tracing init in ``cli.main()``).
    """
    return load_accounts().default.config


def save_accounts(
    config: MailAccountsConfig,
    path: str | os.PathLike[str] | None = None,
) -> None:
    """Persist :class:`MailAccountsConfig` to *path*
    (default ``config/config.json``)."""
    _dump_config(config, path=path)


def get_config_schema() -> str:
    """Return JSON Schema for :class:`MailAccountsConfig`
    (for CI drift check)."""
    return _config_schema_json(MailAccountsConfig)


def load_llm() -> str:
    """Resolve the LLM API key from the config file's ``llm_api_key`` field."""
    try:
        file_cfg = load()
    except Exception:
        return ""
    return file_cfg.llm_api_key._secret_value


def load_llm_provider_model() -> str:
    """Resolve the LLM provider-model from the config file's
    ``llm_provider_model``."""
    try:
        file_cfg = load()
    except Exception:
        return ""
    return file_cfg.llm_provider_model


def resolve_llm_api_key(
    api_key: str | None = None, raise_on_missing: bool = True
) -> str:
    """Resolve the LLM API key: explicit *api_key* arg →
    ``LLM_API_KEY`` env var → config file.

    Args:
        api_key: An explicit key, usually from a CLI parameter.
        raise_on_missing: When ``True`` (the default), raise
            :class:`ConfigurationError` if no key is found.

    Returns:
        The resolved key (may be empty when *raise_on_missing* is ``False``
        and no key is configured).

    Raises:
        ConfigurationError: When *raise_on_missing* is ``True`` and no key
            is found.
    """
    resolved = api_key or os.getenv("LLM_API_KEY") or load_llm()
    if not resolved and raise_on_missing:
        raise ConfigurationError(
            "No LLM API key found — add llm_api_key to config/config.json"
        )
    return resolved


def resolve_llm_provider_model(
    provider_model: str | None = None, default: str = ""
) -> str:
    """Resolve the LLM provider-model: explicit *provider_model* arg →
    ``LLM_PROVIDER_MODEL`` env var → config file.

    Args:
        provider_model: An explicit provider-model identifier, usually from a
            CLI parameter.
        default: Fallback when no provider-model is configured anywhere.

    Returns:
        The resolved provider-model identifier, or *default*.
    """
    resolved = (
        provider_model or os.getenv("LLM_PROVIDER_MODEL") or load_llm_provider_model()
    )
    return resolved or default
