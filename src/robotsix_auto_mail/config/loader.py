"""Configuration loaders: the public ``load*`` entry points.

The single configuration source is the JSON file at ``ROBOTSIX_CONFIG_FILE``
(default ``config/config.json``), which must use the ``accounts:`` shape.
Configuration is loaded exclusively through :mod:`robotsix_config` — there
is no fallback or alternative config path.  Environment variables are NOT
a configuration source.

The two LLM-only resolvers (:func:`resolve_llm_api_key`,
:func:`resolve_llm_tier`) check, in order: an explicit
argument, then the config file's component-wide LLM settings — the
canonical ``openrouter`` block and ``models`` tier overrides, never a
per-account field.

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

from robotsix_auto_mail.config._constants import (
    APP_CLASSIFIER,
    APP_DETECTOR,
    APP_DRAFT,
    APP_RULES,
    APP_TRIAGE,
)
from robotsix_auto_mail.config.credentials import MAIN_LLM_ALIAS, LangfuseConfig
from robotsix_auto_mail.config.model import MailAccountsConfig, MailConfig
from robotsix_auto_mail.config.schema import ConfigurationError

_VALID_APP_NAMES: frozenset[str] = frozenset(
    {
        APP_TRIAGE,
        APP_CLASSIFIER,
        APP_RULES,
        APP_DETECTOR,
        APP_DRAFT,
    }
)

logger = logging.getLogger(__name__)


def load_accounts() -> MailAccountsConfig:
    """Load :class:`MailAccountsConfig` from ``config/config.json``
    (``ROBOTSIX_CONFIG_FILE``)."""
    return _load_config(MailAccountsConfig)


def load() -> MailConfig:
    """Return the **first account's** :class:`MailConfig` from the config file.

    A thin convenience for callers that only need one representative account's
    settings (e.g. the best-effort Langfuse tracing init in ``cli.main()``).
    """
    accounts = load_accounts()
    if not accounts.accounts:
        raise ConfigurationError(
            "No accounts configured — add an account via the web UI "
            "(/add-account) or config file before using this command."
        )
    return accounts.accounts[0].config


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


_MISSING_KEY_HELP = (
    f"No LLM API key found — add openrouter.keys.{MAIN_LLM_ALIAS} to config/config.json"
)


def resolve_llm_api_key(
    api_key: str | None = None, raise_on_missing: bool = True
) -> str:
    """Resolve the LLM API key: explicit *api_key* arg → config file.

    The config-file value is the provider key declared under
    :data:`~robotsix_auto_mail.config.credentials.MAIN_LLM_ALIAS` in the
    canonical ``openrouter`` block.

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
    if api_key:
        return api_key
    try:
        accounts = load_accounts()
    except Exception:
        if raise_on_missing:
            raise ConfigurationError(_MISSING_KEY_HELP) from None
        return ""
    resolved = accounts.openrouter.key()
    if not resolved and raise_on_missing:
        raise ConfigurationError(_MISSING_KEY_HELP)
    return resolved


def resolve_application_level(app_name: str) -> int:
    """Return the configured tier level for a named application.

    *app_name* must be one of the :data:`~._constants.APP_*` constants
    (``"triage"``, ``"classifier"``, ``"rules"``, ``"detector"``, or
    ``"draft"``).

    Returns the configured ``{app_name}_level`` from the config file,
    or ``1`` when the config is unreadable.

    Raises:
        ValueError: if *app_name* is not a recognised application name.
    """
    if app_name not in _VALID_APP_NAMES:
        raise ValueError(
            f"Unknown application name {app_name!r}; "
            f"expected one of {sorted(_VALID_APP_NAMES)}"
        )
    try:
        accounts = load_accounts()
    except Exception:
        return 1
    return getattr(accounts, f"{app_name}_level", 1)


def resolve_model_override(level: int) -> str:
    """Return the ``models.level{N}`` override from the config file.

    Returns an empty string when the override is blank or the config
    is unreadable — the caller is expected to fall back to the llmio
    tier default for that level.
    """
    try:
        accounts = load_accounts()
    except Exception:
        return ""
    return getattr(accounts.models, f"level{level}", "")


def resolve_llm_tier(app_name: str) -> tuple[int, str]:
    """Resolve the LLM tier for a named application.

    Returns a ``(level, model_override)`` pair:
    - *level* is the configured tier level for *app_name*.
    - *model_override* is the ``models.level{N}`` override string
      (empty when not set — the caller passes it through to
      :func:`~robotsix_auto_mail.core._llm_agent._run_llm_agent`
      which falls back to the llmio tier default).

    This is the single entry point callers should use instead of the
    old ``resolve_llm_provider_model``.
    """
    return (
        resolve_application_level(app_name),
        resolve_model_override(resolve_application_level(app_name)),
    )


def get_resolved_models() -> dict[str, str]:
    """Return the effective model for each tier level (1-4) for display.

    For each level, returns the config override if set, otherwise the
    resolved llmio ``LEVELn_DEFAULT`` model identifier.  Useful for
    config panels that need to show the effective model even when the
    override field is blank.

    Returns an empty dict when the config or llmio is unreadable.
    """
    from robotsix_llmio.config.tier import (
        LEVEL1_DEFAULT,
        LEVEL2_DEFAULT,
        LEVEL3_DEFAULT,
    )

    llmio_defaults: dict[int, str] = {
        1: LEVEL1_DEFAULT.model,
        2: LEVEL2_DEFAULT.model,
        3: LEVEL3_DEFAULT.model,
        4: _LEVEL4_DEFAULT_MODEL,
    }
    result: dict[str, str] = {}
    for level in (1, 2, 3, 4):
        override = resolve_model_override(level)
        result[f"level{level}"] = override or llmio_defaults.get(level, "")
    return result


#: Provider-model identifier used as the level-4 fallback when llmio
#: does not yet define a ``LEVEL4_DEFAULT``.
_LEVEL4_DEFAULT_MODEL = "claudeSDK-opus"


def load_langfuse() -> LangfuseConfig:
    """The canonical ``langfuse`` block, or an empty one when unreadable.

    Tracing setup must never be the reason the process fails to start, so a
    missing or invalid config file yields an unconfigured block rather than
    an exception.
    """
    try:
        return load_accounts().langfuse
    except Exception:
        logger.debug("Config unreadable; Langfuse tracing left unconfigured")
        return LangfuseConfig()
