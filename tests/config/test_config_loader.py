"""Unit tests for the config loader module (loader.py).

Configuration is read exclusively from the JSON config file via
``robotsix_config``.  Covers load(), load_accounts(), load_llm(),
load_llm_provider_model(), resolve_llm_api_key() and
resolve_llm_provider_model().
"""

from __future__ import annotations

from unittest import mock

import pytest

from robotsix_auto_mail.config import (
    ConfigurationError,
    MailAccount,
    MailAccountsConfig,
    MailConfig,
    load,
    load_accounts,
    load_llm,
    load_llm_provider_model,
    resolve_llm_api_key,
    resolve_llm_provider_model,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _default_accounts(
    *,
    llm_api_key: str = "",
    llm_provider_model: str = "",
) -> MailAccountsConfig:
    """Return a minimal single-account config."""
    return MailAccountsConfig(
        accounts=[
            MailAccount(
                account_id="default",
                config=MailConfig(
                    imap_host="imap.example.com",
                    smtp_host="smtp.example.com",
                    username="user@example.com",
                    password="pass",
                    llm_api_key=llm_api_key,
                    llm_provider_model=llm_provider_model,
                ),
            )
        ],
        default_account_id="default",
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
# load_llm()
# ---------------------------------------------------------------------------


def test_load_llm_reads_config() -> None:
    """load_llm reads llm_api_key from the config."""
    accts = _default_accounts(llm_api_key="sk-from-file")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert load_llm() == "sk-from-file"


def test_load_llm_config_without_llm_section() -> None:
    """Config without llm_api_key yields empty string."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        return_value=_default_accounts(),
    ):
        assert load_llm() == ""


def test_load_llm_missing_config_file() -> None:
    """When config loading fails, load_llm returns ''."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        side_effect=ConfigurationError("no config"),
    ):
        assert load_llm() == ""


def test_load_llm_config_load_error() -> None:
    """When load_accounts raises ConfigurationError, returns ''."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        side_effect=ConfigurationError("boom"),
    ):
        assert load_llm() == ""


# ---------------------------------------------------------------------------
# load_llm_provider_model()
# ---------------------------------------------------------------------------


def test_load_llm_provider_model_reads_config() -> None:
    """load_llm_provider_model reads llm_provider_model from the config."""
    accts = _default_accounts(llm_provider_model="yaml-model")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert load_llm_provider_model() == "yaml-model"


def test_load_llm_provider_model_config_without_llm_section() -> None:
    """Config without llm_provider_model → empty string."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        return_value=_default_accounts(),
    ):
        assert load_llm_provider_model() == ""


def test_load_llm_provider_model_config_without_provider_model_key() -> None:
    """llm_api_key present but provider_model absent → empty string."""
    accts = _default_accounts(llm_api_key="k")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert load_llm_provider_model() == ""


# ---------------------------------------------------------------------------
# resolve_llm_api_key()
# ---------------------------------------------------------------------------


def test_resolve_llm_api_key_explicit_arg_wins() -> None:
    """An explicit api_key argument is the top priority."""
    assert resolve_llm_api_key("explicit-key") == "explicit-key"


def test_resolve_llm_api_key_falls_back_to_file() -> None:
    """No arg → falls back to the config file's llm_api_key."""
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


def test_resolve_llm_api_key_env_var_no_longer_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_API_KEY env var is NOT picked up (removed from resolve_llm_api_key)."""
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        return_value=_default_accounts(),
    ):
        # LLM_API_KEY env var is not consulted → no key found
        assert resolve_llm_api_key(raise_on_missing=False) == ""


def test_resolve_llm_api_key_explicit_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit api_key arg wins over LLM_API_KEY env var."""
    monkeypatch.setenv("LLM_API_KEY", "env-key")
    assert resolve_llm_api_key("explicit-key") == "explicit-key"


# ---------------------------------------------------------------------------
# resolve_llm_provider_model()
# ---------------------------------------------------------------------------


def test_resolve_llm_provider_model_explicit_arg_wins() -> None:
    """An explicit provider_model argument is the top priority."""
    assert resolve_llm_provider_model("explicit-model") == "explicit-model"


def test_resolve_llm_provider_model_falls_back_to_file() -> None:
    """No arg → falls back to the config file's llm_provider_model."""
    accts = _default_accounts(llm_provider_model="yaml-model")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert resolve_llm_provider_model() == "yaml-model"


def test_resolve_llm_provider_model_caller_default() -> None:
    """When nothing is configured, the caller-supplied default is used."""
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        return_value=_default_accounts(),
    ):
        assert resolve_llm_provider_model(default="my-default") == "my-default"


def test_resolve_llm_provider_model_explicit_empty_falls_through() -> None:
    """Empty string arg falls through to the file/default."""
    accts = _default_accounts(llm_provider_model="yaml-model")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts", return_value=accts
    ):
        assert resolve_llm_provider_model("") == "yaml-model"


def test_resolve_llm_provider_model_env_var_no_longer_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM_PROVIDER_MODEL env var is NOT picked up (removed)."""
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "env-model")
    with mock.patch(
        "robotsix_auto_mail.config.loader.load_accounts",
        return_value=_default_accounts(),
    ):
        # LLM_PROVIDER_MODEL env var is not consulted → uses default
        assert resolve_llm_provider_model() == ""


def test_resolve_llm_provider_model_explicit_wins_over_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit provider_model arg wins over LLM_PROVIDER_MODEL env var."""
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "env-model")
    assert resolve_llm_provider_model("explicit-model") == "explicit-model"


# ---------------------------------------------------------------------------
# load_accounts()
# ---------------------------------------------------------------------------


def test_load_accounts_returns_config() -> None:
    """load_accounts returns the config via robotsix_config."""
    import sys as _sys

    accts = _default_accounts()
    mock_rc = mock.MagicMock()
    mock_rc.load_config = mock.MagicMock(return_value=accts)
    with mock.patch.dict(_sys.modules, {"robotsix_config": mock_rc}):
        accounts = load_accounts()
    assert isinstance(accounts, MailAccountsConfig)
    cfg = accounts.default.config
    assert cfg.imap_host == "imap.example.com"
    assert cfg.username == "user@example.com"


def test_load_accounts_missing_file_raises() -> None:
    """When robotsix_config raises, load_accounts falls back to direct load,
    which raises ConfigurationError when config/config.json is missing."""
    import sys as _sys

    mock_rc = mock.MagicMock()
    mock_rc.load_config = mock.MagicMock(
        side_effect=FileNotFoundError("no config"),
    )
    with (
        mock.patch.dict(_sys.modules, {"robotsix_config": mock_rc}),
        # Ensure the fallback can't read a local config file — the test
        # must be deterministic regardless of what's on disk.
        mock.patch("pathlib.Path.read_text", side_effect=FileNotFoundError),
    ):
        with pytest.raises(ConfigurationError, match="No valid configuration found"):
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
