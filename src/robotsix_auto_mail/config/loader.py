"""Configuration loaders: the public ``load*`` entry points.

The primary configuration source is the YAML file at ``MAIL_CONFIG_PATH``
(default ``config/mail.local.yaml``), which must use the multi-account
``accounts:`` shape.  ``MAIL_CONFIG_PATH`` only *locates* the file — it is
not a general environment-variable config path.

The two LLM-only resolvers (:func:`resolve_llm_api_key`,
:func:`resolve_llm_provider_model`) additionally consult the
``LLM_API_KEY`` and ``LLM_PROVIDER_MODEL`` environment variables as a
fallback tier between explicit arguments and the YAML file.  The remaining
loaders (``load_accounts``, ``load``, etc.) are YAML-only.

Depends on :mod:`robotsix_auto_mail.config.model`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from robotsix_yaml_config import (
    YamlConfigError,
    read_yaml_file,
)

from robotsix_auto_mail.config.model import MailAccountsConfig, MailConfig
from robotsix_auto_mail.config.schema import ConfigurationError, _mono_shape_error

logger = logging.getLogger(__name__)

# Default YAML config file path (used when ``MAIL_CONFIG_PATH`` is unset).
DEFAULT_CONFIG_PATH = "config/mail.local.yaml"


def _config_path() -> Path:
    """Return the config file path (``MAIL_CONFIG_PATH`` or the default)."""
    return Path(os.environ.get("MAIL_CONFIG_PATH", DEFAULT_CONFIG_PATH))


def load_accounts() -> MailAccountsConfig:
    """Load :class:`MailAccountsConfig` from the YAML config file.

    Reads the file at ``MAIL_CONFIG_PATH`` (default ``config/mail.local.yaml``),
    which must use the multi-account ``accounts:`` shape.  A missing file or a
    single-account ("mono") shape raises an actionable
    :class:`ConfigurationError` (the latter naming ``detect``).
    """
    config_path = _config_path()
    if not config_path.exists():
        raise ConfigurationError(
            f"No config file found at {config_path}. Create one with an "
            f"`accounts:` list (run `detect` to generate one), or point "
            f"MAIL_CONFIG_PATH at an existing config file."
        )
    try:
        data = read_yaml_file(config_path)
    except YamlConfigError as exc:
        raise ConfigurationError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not (isinstance(data, dict) and isinstance(data.get("accounts"), list)):
        # A single-account ("mono") shape is no longer supported.
        raise ConfigurationError(_mono_shape_error(config_path))

    result = MailAccountsConfig.from_yaml(config_path)
    _log_failed_accounts(result)
    return result


def load() -> MailConfig:
    """Return the **default account's** :class:`MailConfig` from the config file.

    A thin convenience for callers that only need one representative account's
    settings (e.g. the best-effort Langfuse tracing init in ``cli.main()``).
    """
    return load_accounts().default.config


def _load_file_config_optional() -> MailConfig | None:
    """Return the default account's :class:`MailConfig`, or ``None``.

    Returns ``None`` when the config file does not exist or cannot be parsed
    as a multi-account config.  Used by the LLM-only resolvers, which run
    before a complete mail configuration is guaranteed (e.g. ``detect``).
    """
    config_path = _config_path()
    if not config_path.exists():
        return None
    try:
        accounts = MailAccountsConfig.from_yaml(config_path, validate=False)
        return accounts.default.config
    except ConfigurationError, FileNotFoundError, OSError:
        return None


def load_llm() -> str:
    """Resolve the LLM API key from the config file's ``llm.api_key`` field."""
    file_cfg = _load_file_config_optional()
    return file_cfg.llm_api_key if file_cfg is not None else ""


def load_llm_provider_model() -> str:
    """Resolve the LLM provider-model from the config file's ``llm.provider_model``."""
    file_cfg = _load_file_config_optional()
    return file_cfg.llm_provider_model if file_cfg is not None else ""


def resolve_llm_api_key(
    api_key: str | None = None, raise_on_missing: bool = True
) -> str:
    """Resolve the LLM API key: explicit *api_key* arg → env var → config file.

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
            "No LLM API key found — set the LLM_API_KEY environment variable"
            " or add an `llm.api_key` entry to your config file"
        )
    return resolved


def resolve_llm_provider_model(
    provider_model: str | None = None, default: str = ""
) -> str:
    """Resolve the LLM provider-model: explicit *provider_model* arg → env var →
    config file.

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


def _log_failed_accounts(result: MailAccountsConfig) -> None:
    """Log each failed account entry at ERROR level so it surfaces in logs."""
    for entry in result.failed_accounts:
        logger.error(
            "Account %r skipped due to config error: %s",
            entry.account_id,
            entry.error,
        )
