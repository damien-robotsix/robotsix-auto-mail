"""Unit tests for the config loader module (loader.py).

Configuration is read exclusively from the JSON config file via
``robotsix_config``.  Covers load(), load_accounts(),
resolve_llm_api_key(), resolve_llm_tier(), resolve_application_level(),
resolve_model_override(), and get_resolved_models().
"""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import (
    APP_CLASSIFIER,
    APP_DRAFT,
    APP_TRIAGE,
    MAIN_LLM_ALIAS,
    ConfigurationError,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    OpenRouterConfig,
    TierModelsConfig,
    get_resolved_models,
    load,
    load_accounts,
    resolve_application_level,
    resolve_llm_api_key,
    resolve_llm_tier,
    resolve_model_override,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_accounts(
    *,
    llm_api_key: str = "",
    models: TierModelsConfig | None = None,
    triage_level: int = 1,
    classifier_level: int = 1,
    rules_level: int = 1,
    detector_level: int = 1,
    draft_level: int = 1,
) -> MailAccountsConfig:
    """Return a minimal single-account config.

    The LLM settings are component-wide: the provider key goes in the
    canonical ``openrouter`` block under the component's alias, not on the
    account.  Model overrides live in ``models``.
    """
    return MailAccountsConfig(
        accounts=[
            MailAccount(
                account_id="default",
                config=MailConfig(
                    imap_host="imap.example.com",
                    smtp_host="smtp.example.com",
                    username="user@example.com",
                    password="pass",
                ),
            )
        ],
        openrouter=OpenRouterConfig(keys={MAIN_LLM_ALIAS: llm_api_key}),
        models=models or TierModelsConfig(),
        triage_level=triage_level,
        classifier_level=classifier_level,
        rules_level=rules_level,
        detector_level=detector_level,
        draft_level=draft_level,
    )


def _patch_load_accounts(
    monkeypatch: pytest.MonkeyPatch,
    accounts: MailAccountsConfig | None = None,
) -> MailAccountsConfig:
    """Mock load_accounts to return *accounts*."""
    if accounts is None:
        accounts = _default_accounts()
    monkeypatch.setattr(
        "robotsix_auto_mail.config.loader.load_accounts",
        lambda: accounts,
    )
    return accounts


# ---------------------------------------------------------------------------
# resolve_llm_api_key()
# ---------------------------------------------------------------------------


def test_resolve_llm_api_key_explicit_arg_wins() -> None:
    """An explicit api_key argument is the top priority."""
    assert resolve_llm_api_key("explicit-key") == "explicit-key"


def test_resolve_llm_api_key_falls_back_to_file() -> None:
    """No arg → falls back to the config file's openrouter key."""
    accts = _default_accounts(llm_api_key="sk-from-file")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert resolve_llm_api_key() == "sk-from-file"


def test_resolve_llm_api_key_raise_on_missing_true() -> None:
    """raise_on_missing=True and no key anywhere → ConfigurationError."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        return_value=_default_accounts(),
    ):
        with pytest.raises(ConfigurationError, match="No LLM API key found"):
            resolve_llm_api_key()


def test_resolve_llm_api_key_raise_on_missing_false() -> None:
    """raise_on_missing=False and no key anywhere → empty string."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        return_value=_default_accounts(),
    ):
        assert resolve_llm_api_key(raise_on_missing=False) == ""


def test_resolve_llm_api_key_explicit_empty_string_falls_through() -> None:
    """An explicit empty string falls through to the file, just like None."""
    accts = _default_accounts(llm_api_key="sk-from-file")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert resolve_llm_api_key("") == "sk-from-file"


def test_resolve_llm_api_key_no_env_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_API_KEY env var is NOT consulted — only explicit arg and config file."""
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        return_value=_default_accounts(),
    ):
        # Env var is ignored; no key in config file → empty string.
        assert resolve_llm_api_key(raise_on_missing=False) == ""


def test_resolve_llm_api_key_explicit_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit api_key arg wins over LLM_API_KEY env var."""
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    assert resolve_llm_api_key("explicit-key") == "explicit-key"


# ---------------------------------------------------------------------------
# resolve_application_level()
# ---------------------------------------------------------------------------


def test_resolve_application_level_defaults_to_1() -> None:
    """When config is unreadable or level not set, default to 1."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        side_effect=ConfigurationError("no config"),
    ):
        assert resolve_application_level(APP_TRIAGE) == 1


def test_resolve_application_level_reads_config() -> None:
    """Reads the configured {app}_level from the file."""
    accts = _default_accounts(triage_level=3, draft_level=2)
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert resolve_application_level(APP_TRIAGE) == 3
        assert resolve_application_level(APP_DRAFT) == 2
        assert resolve_application_level(APP_CLASSIFIER) == 1  # default


def test_resolve_application_level_rejects_unknown_app_name() -> None:
    """Passing an unrecognised app name raises ValueError."""
    with pytest.raises(ValueError, match="Unknown application name"):
        resolve_application_level("nonexistent_app")


# ---------------------------------------------------------------------------
# resolve_model_override()
# ---------------------------------------------------------------------------


def test_resolve_model_override_returns_empty_when_not_set() -> None:
    """Empty override returns empty string."""
    accts = _default_accounts()
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert resolve_model_override(1) == ""


def test_resolve_model_override_returns_configured_value() -> None:
    """Configured override is returned."""
    accts = _default_accounts(
        models=TierModelsConfig(level1="my-model", level3="other-model")
    )
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert resolve_model_override(1) == "my-model"
        assert resolve_model_override(2) == ""
        assert resolve_model_override(3) == "other-model"


def test_resolve_model_override_config_unreadable_returns_empty() -> None:
    """When config is unreadable, return empty string."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        side_effect=ConfigurationError("no config"),
    ):
        assert resolve_model_override(1) == ""


# ---------------------------------------------------------------------------
# resolve_llm_tier()
# ---------------------------------------------------------------------------


def test_resolve_llm_tier_returns_level_and_override() -> None:
    """Returns (level, model_override) for the named application."""
    accts = _default_accounts(
        triage_level=2,
        models=TierModelsConfig(level2="my-tier-model"),
    )
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        level, pm = resolve_llm_tier(APP_TRIAGE)
        assert level == 2
        assert pm == "my-tier-model"


def test_resolve_llm_tier_empty_override() -> None:
    """When models.level{N} is empty, returns empty override string."""
    accts = _default_accounts(draft_level=3)
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        level, pm = resolve_llm_tier(APP_DRAFT)
        assert level == 3
        assert pm == ""


# ---------------------------------------------------------------------------
# get_resolved_models()
# ---------------------------------------------------------------------------


def test_get_resolved_models_includes_llmio_defaults() -> None:
    """When overrides are blank, returns the llmio tier defaults."""
    accts = _default_accounts()
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        resolved = get_resolved_models()
        assert resolved["level1"] != ""
        assert resolved["level2"] != ""
        assert resolved["level3"] != ""
        assert resolved["level4"] != ""
        # level1 should be the llmio default (deepseek flash)
        assert (
            "deepseek" in resolved["level1"].lower()
            or "flash" in resolved["level1"].lower()
        )


# ---------------------------------------------------------------------------
# load_accounts()
# ---------------------------------------------------------------------------


def test_load_accounts_returns_config() -> None:
    """load_accounts returns the config via robotsix_config."""
    accts = _default_accounts()
    with mock.patch(
        "robotsix_auto_mail.config.loader._load_config", return_value=accts
    ):
        accounts = load_accounts()
    assert isinstance(accounts, MailAccountsConfig)
    cfg = accounts.accounts[0].config
    assert cfg.imap_host == "imap.example.com"
    assert cfg.username == "user@example.com"


def test_load_accounts_missing_file_raises() -> None:
    """When robotsix_config raises InvalidConfigError, load_accounts propagates it."""
    from robotsix_config import InvalidConfigError

    with mock.patch(
        "robotsix_auto_mail.config.loader._load_config",
        side_effect=InvalidConfigError("Config in config/config.json is invalid"),
    ):
        with pytest.raises(InvalidConfigError):
            load_accounts()


# ---------------------------------------------------------------------------
# load() convenience function
# ---------------------------------------------------------------------------


def test_load_reads_config() -> None:
    """load() delegates to load_accounts and returns the default account config."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        return_value=_default_accounts(),
    ):
        cfg = load()
    assert isinstance(cfg, MailConfig)
    assert cfg.imap_host == "imap.example.com"


def test_load_missing_config_file() -> None:
    """A missing config file → ConfigurationError."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        side_effect=ConfigurationError("no config"),
    ):
        with pytest.raises(ConfigurationError):
            load()
