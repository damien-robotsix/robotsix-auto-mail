"""Configuration loaders: the public ``load*`` entry points.

The primary configuration source is the JSON file at ``ROBOTSIX_CONFIG_FILE``
(default ``config/config.json``), which must use the ``accounts:`` shape.
``ROBOTSIX_CONFIG_FILE`` only *locates* the file — it is not a general
environment-variable config path.

The two LLM-only resolvers (:func:`resolve_llm_api_key`,
:func:`resolve_llm_provider_model`) check, in order: an explicit
argument, then the ``LLM_API_KEY`` / ``LLM_PROVIDER_MODEL``
environment variable, then the config file.

Depends on :mod:`robotsix_auto_mail.config.model`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from robotsix_auto_mail.config.model import MailAccountsConfig, MailConfig
from robotsix_auto_mail.config.schema import ConfigurationError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _unwrap_secrets(obj: Any) -> Any:
    """Recursively replace :class:`SecretStr` values with their raw strings.

    Used when writing the config to disk so that credentials are preserved
    in the JSON file (``model_dump_json`` would otherwise mask them).
    """
    if isinstance(obj, SecretStr):
        return obj.get_secret_value()
    if isinstance(obj, dict):
        return {k: _unwrap_secrets(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_unwrap_secrets(v) for v in obj]
    return obj


def _dump_config_json(config: MailAccountsConfig) -> str:
    """Serialize *config* to a JSON string with secrets exposed."""
    import json as _json

    data = config.model_dump(mode="python")
    return _json.dumps(_unwrap_secrets(data), indent=2, ensure_ascii=False)  # lgtm[py/clear-text-storage-sensitive-data]


def load_accounts() -> MailAccountsConfig:
    """Load :class:`MailAccountsConfig` from ``config/config.json``
    (``ROBOTSIX_CONFIG_FILE``)."""
    try:
        from robotsix_config import load_config as _load_config
    except ModuleNotFoundError:
        logger.debug("robotsix_config not installed — falling back to direct load")
        return _load_accounts_fallback()
    try:
        return _load_config(MailAccountsConfig)
    except Exception:
        logger.debug("robotsix_config load failed — falling back to direct load")
        return _load_accounts_fallback()


def _resolve_config_path() -> Path:
    """Return the path to the config file, respecting ``ROBOTSIX_CONFIG_FILE``."""
    import os as _os

    env = _os.environ.get("ROBOTSIX_CONFIG_FILE")
    if env:
        return Path(env)
    return Path("config/config.json")


def _load_accounts_fallback() -> MailAccountsConfig:
    """Directly read the config file when ``robotsix_config`` is unavailable."""
    import json as _json

    path = _resolve_config_path()
    try:
        text = path.read_text()
        return MailAccountsConfig.model_validate(_json.loads(text))
    except Exception:
        logger.debug("Cannot load config from %s — returning empty config", path)
        try:
            return MailAccountsConfig(accounts=[], default_account_id="")
        except Exception:
            from robotsix_auto_mail.config.schema import ConfigurationError

            raise ConfigurationError(
                f"No valid configuration found at {path}. "
                "Run 'robotsix-auto-mail detect' to create one."
            ) from None


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
    try:
        from robotsix_config import dump_config as _dump_config
    except ModuleNotFoundError:
        logger.debug("robotsix_config not installed — writing JSON directly")
        target = Path(path) if path is not None else _resolve_config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_dump_config_json(config) + "\n")  # lgtm[py/clear-text-storage-sensitive-data]
        return
    _dump_config(config, path=path)


def get_config_schema() -> str:
    """Return JSON Schema for :class:`MailAccountsConfig`
    (for CI drift check)."""
    try:
        from robotsix_config import config_schema_json as _config_schema_json
    except ModuleNotFoundError:
        logger.debug("robotsix_config not installed — returning empty schema")
        return "{}"
    return _config_schema_json(MailAccountsConfig)


def load_llm() -> str:
    """Resolve the LLM API key from the config file's ``llm_api_key`` field."""
    try:
        file_cfg = load()
    except Exception:
        return ""
    return file_cfg.llm_api_key.get_secret_value()


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
