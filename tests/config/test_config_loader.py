"""Unit tests for the config loader module (loader.py).

Covers load(), load_llm(), load_llm_provider_model(),
resolve_llm_api_key(), resolve_llm_provider_model(), and load_accounts().
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from robotsix_auto_mail.config import (
    ConfigurationError,
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


def _multi_account_yaml(tmp_path: Path, **overrides: str) -> Path:
    """Write a minimal multi-account YAML file and return its path.

    Overrides can supply ``llm_api_key``, ``llm_provider_model``, etc.
    """
    yaml_file = tmp_path / "mail.local.yaml"
    llm_api_key = overrides.pop("llm_api_key", None)
    llm_provider_model = overrides.pop("llm_provider_model", None)
    lines = []
    if llm_api_key or llm_provider_model:
        lines.append("llm:")
        if llm_api_key:
            lines.append(f"  api_key: {llm_api_key}")
        if llm_provider_model:
            lines.append(f"  provider_model: {llm_provider_model}")

    lines += [
        "accounts:",
        "  - id: default",
        "    imap:",
        "      host: imap.example.com",
        "    smtp:",
        "      host: smtp.example.com",
        "    auth:",
        "      username: user@example.com",
        "      password: pass",
    ]

    yaml_file.write_text("\n".join(lines) + "\n")
    return yaml_file


def _mono_yaml(tmp_path: Path) -> Path:
    """Write a legacy single-account (mono) YAML file."""
    yaml_file = tmp_path / "mono.yaml"
    yaml_file.write_text(
        """\
imap:
  host: imap.example.com

smtp:
  host: smtp.example.com

auth:
  username: user@example.com
  password: pass
"""
    )
    return yaml_file


def _env_with_path(path: str | Path) -> dict[str, str]:
    """Return a minimal env dict pointing at *path*."""
    return {"MAIL_CONFIG_PATH": str(path)}


# ---------------------------------------------------------------------------
# load_llm()
# ---------------------------------------------------------------------------


def test_load_llm_env_wins() -> None:
    """LLM_API_KEY env var is the top priority."""
    env: dict[str, str] = {
        "LLM_API_KEY": "sk-env",
        "MAIL_CONFIG_PATH": "/nonexistent/mail.yaml",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        assert load_llm() == "sk-env"


def test_load_llm_env_empty_string_means_not_set(tmp_path: Path) -> None:
    """An empty LLM_API_KEY is treated as not set."""
    yaml_file = _multi_account_yaml(tmp_path, llm_api_key="sk-from-file")
    env: dict[str, str] = {
        "LLM_API_KEY": "",
        "MAIL_CONFIG_PATH": str(yaml_file),
    }
    with mock.patch.dict(os.environ, env, clear=True):
        assert load_llm() == "sk-from-file"


def test_load_llm_falls_back_to_yaml(tmp_path: Path) -> None:
    """When env is absent, load_llm reads llm.api_key from the YAML file."""
    yaml_file = _multi_account_yaml(tmp_path, llm_api_key="sk-from-file")
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        assert load_llm() == "sk-from-file"


def test_load_llm_yaml_without_llm_section(tmp_path: Path) -> None:
    """A YAML file without a top-level llm: section yields empty string."""
    yaml_file = _multi_account_yaml(tmp_path)
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        assert load_llm() == ""


def test_load_llm_missing_config_file() -> None:
    """When MAIL_CONFIG_PATH points to a missing file, load_llm returns ''."""
    with mock.patch.dict(
        os.environ, {"MAIL_CONFIG_PATH": "/nonexistent/mail.yaml"}, clear=True
    ):
        assert load_llm() == ""


def test_load_llm_default_config_path_when_env_empty() -> None:
    """When MAIL_CONFIG_PATH is not set and no LLM_API_KEY, returns ''."""
    with mock.patch.dict(os.environ, {}, clear=True):
        # The default config file does not exist in this repo, so fallback is ''
        assert load_llm() == ""


def test_load_llm_yaml_with_env_llm_none(tmp_path: Path) -> None:
    """A YAML file with api_key: in the top-level llm: section but
    the accounts have valid config — still reads the top-level key."""
    yaml_file = tmp_path / "mail.local.yaml"
    yaml_file.write_text(
        """\
llm:
  api_key: sk-top-level
accounts:
  - id: default
    imap:
      host: imap.example.com
    smtp:
      host: smtp.example.com
    auth:
      username: u
      password: p
"""
    )
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        assert load_llm() == "sk-top-level"


# ---------------------------------------------------------------------------
# load_llm_provider_model()
# ---------------------------------------------------------------------------


def test_load_llm_provider_model_env_wins() -> None:
    """LLM_PROVIDER_MODEL env var is the top priority."""
    env: dict[str, str] = {
        "LLM_PROVIDER_MODEL": "env-model",
        "MAIL_CONFIG_PATH": "/nonexistent/mail.yaml",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        assert load_llm_provider_model() == "env-model"


def test_load_llm_provider_model_env_empty_falls_back_to_yaml(tmp_path: Path) -> None:
    """When env is absent, falls back to llm.provider_model in YAML."""
    yaml_file = _multi_account_yaml(tmp_path, llm_provider_model="yaml-model")
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        assert load_llm_provider_model() == "yaml-model"


def test_load_llm_provider_model_default_when_nothing_set() -> None:
    """When nothing is set, returns the hard-coded default 'openrouter-deepseek'."""
    with mock.patch.dict(
        os.environ, {"MAIL_CONFIG_PATH": "/nonexistent/mail.yaml"}, clear=True
    ):
        assert load_llm_provider_model() == "openrouter-deepseek"


def test_load_llm_provider_model_yaml_without_llm_section(tmp_path: Path) -> None:
    """A YAML file without llm section → default 'openrouter-deepseek'."""
    yaml_file = _multi_account_yaml(tmp_path)
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        assert load_llm_provider_model() == "openrouter-deepseek"


def test_load_llm_provider_model_yaml_without_provider_model_key(
    tmp_path: Path,
) -> None:
    """llm: section present but without provider_model → default."""
    yaml_file = _multi_account_yaml(tmp_path, llm_api_key="k")
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        assert load_llm_provider_model() == "openrouter-deepseek"


# ---------------------------------------------------------------------------
# resolve_llm_api_key()
# ---------------------------------------------------------------------------


def test_resolve_llm_api_key_explicit_arg_wins() -> None:
    """An explicit api_key argument is the top priority."""
    with mock.patch.dict(os.environ, {}, clear=True):
        assert resolve_llm_api_key("explicit-key") == "explicit-key"


def test_resolve_llm_api_key_env_wins_over_file() -> None:
    """LLM_API_KEY env wins over config file when no explicit arg."""
    env: dict[str, str] = {
        "LLM_API_KEY": "sk-env",
        "MAIL_CONFIG_PATH": "/nonexistent/mail.yaml",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        assert resolve_llm_api_key() == "sk-env"


def test_resolve_llm_api_key_falls_back_to_file(tmp_path: Path) -> None:
    """No arg, no env → falls back to config file."""
    yaml_file = _multi_account_yaml(tmp_path, llm_api_key="sk-from-file")
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        assert resolve_llm_api_key() == "sk-from-file"


def test_resolve_llm_api_key_raise_on_missing_true() -> None:
    """raises ConfigurationError when no key anywhere and raise_on_missing=True."""
    with mock.patch.dict(
        os.environ, {"MAIL_CONFIG_PATH": "/nonexistent/mail.yaml"}, clear=True
    ):
        with pytest.raises(ConfigurationError, match="No LLM API key found"):
            resolve_llm_api_key()


def test_resolve_llm_api_key_raise_on_missing_false() -> None:
    """Returns empty string when no key anywhere and raise_on_missing=False."""
    with mock.patch.dict(
        os.environ, {"MAIL_CONFIG_PATH": "/nonexistent/mail.yaml"}, clear=True
    ):
        assert resolve_llm_api_key(raise_on_missing=False) == ""


def test_resolve_llm_api_key_explicit_empty_string(tmp_path: Path) -> None:
    """An explicit empty string falls through to env/file just like None."""
    yaml_file = _multi_account_yaml(tmp_path, llm_api_key="sk-from-file")
    env: dict[str, str] = {
        "LLM_API_KEY": "",
        "MAIL_CONFIG_PATH": str(yaml_file),
    }
    with mock.patch.dict(os.environ, env, clear=True):
        assert resolve_llm_api_key("") == "sk-from-file"


# ---------------------------------------------------------------------------
# resolve_llm_provider_model()
# ---------------------------------------------------------------------------


def test_resolve_llm_provider_model_explicit_arg_wins() -> None:
    """An explicit provider_model argument is the top priority."""
    with mock.patch.dict(os.environ, {}, clear=True):
        assert resolve_llm_provider_model("explicit-model") == "explicit-model"


def test_resolve_llm_provider_model_env_wins_over_file() -> None:
    """LLM_PROVIDER_MODEL env wins over config file."""
    env: dict[str, str] = {
        "LLM_PROVIDER_MODEL": "env-model",
        "MAIL_CONFIG_PATH": "/nonexistent/mail.yaml",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        assert resolve_llm_provider_model() == "env-model"


def test_resolve_llm_provider_model_falls_back_to_file(tmp_path: Path) -> None:
    """No arg, no env → falls back to config file."""
    yaml_file = _multi_account_yaml(tmp_path, llm_provider_model="yaml-model")
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        assert resolve_llm_provider_model() == "yaml-model"


def test_resolve_llm_provider_model_caller_default() -> None:
    """When load_llm_provider_model returns '' (e.g. env/file both empty),
    the caller-supplied default is used."""
    with mock.patch.dict(
        os.environ, {"MAIL_CONFIG_PATH": "/nonexistent/mail.yaml"}, clear=True
    ):
        with mock.patch(
            "robotsix_auto_mail.config.loader.load_llm_provider_model",
            return_value="",
        ):
            assert resolve_llm_provider_model(default="my-default") == "my-default"


def test_resolve_llm_provider_model_caller_default_overridden() -> None:
    """Caller default is overridden by env var."""
    env: dict[str, str] = {
        "LLM_PROVIDER_MODEL": "env-model",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        assert resolve_llm_provider_model(default="my-default") == "env-model"


def test_resolve_llm_provider_model_explicit_empty_falls_through(
    tmp_path: Path,
) -> None:
    """Empty string arg falls through to env/file/default."""
    yaml_file = _multi_account_yaml(tmp_path, llm_provider_model="yaml-model")
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        assert resolve_llm_provider_model("") == "yaml-model"


# ---------------------------------------------------------------------------
# load_accounts()
# ---------------------------------------------------------------------------


def test_load_accounts_from_env_only() -> None:
    """load_accounts() returns config from env vars when fully specified."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        accounts = load_accounts()
        assert isinstance(accounts, MailAccountsConfig)
        cfg = accounts.default.config
        assert cfg.imap_host == "imap.env.com"
        assert cfg.username == "env_user"


def test_load_accounts_falls_back_to_yaml(tmp_path: Path) -> None:
    """When env is incomplete, load_accounts falls back to YAML file."""
    yaml_file = _multi_account_yaml(tmp_path)
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        accounts = load_accounts()
        assert isinstance(accounts, MailAccountsConfig)
        cfg = accounts.default.config
        assert cfg.imap_host == "imap.example.com"
        assert cfg.username == "user@example.com"


def test_load_accounts_invalid_value_re_raises(tmp_path: Path) -> None:
    """An invalid env value (e.g. non-numeric port) is re-raised, not
    silently swallowed by YAML fallback."""
    yaml_file = _multi_account_yaml(tmp_path)
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(yaml_file),
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
        "MAIL_IMAP_PORT": "not-a-number",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            load_accounts()
        msg = str(exc.value)
        assert "MAIL_IMAP_PORT" in msg
        assert "not-a-number" in msg


def test_load_accounts_missing_env_and_no_file() -> None:
    """No env vars AND no config file → ConfigurationError."""
    env: dict[str, str] = {"MAIL_CONFIG_PATH": "/nonexistent/mail.yaml"}
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError):
            load_accounts()


def test_load_accounts_missing_env_and_no_path() -> None:
    """MAIL_CONFIG_PATH not set AND no default file → ConfigurationError."""
    with mock.patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ConfigurationError):
            load_accounts()


def test_load_accounts_mono_yaml_raises(tmp_path: Path) -> None:
    """A legacy single-account (mono) YAML file raises ConfigurationError
    with the migrate-config/detect message."""
    yaml_file = _mono_yaml(tmp_path)
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        with pytest.raises(ConfigurationError) as exc:
            load_accounts()
        msg = str(exc.value)
        assert "single-account" in msg
        assert "migrate-config" in msg
        assert "detect" in msg


def test_load_accounts_yaml_without_accounts_key(tmp_path: Path) -> None:
    """A YAML file that exists but has no 'accounts' key and is not mono
    (just an empty doc) falls through to the env error path."""
    yaml_file = tmp_path / "empty.yaml"
    yaml_file.write_text("{}\n")
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        with pytest.raises(ConfigurationError):
            load_accounts()


def test_load_accounts_invalid_yaml_raises(tmp_path: Path) -> None:
    """A syntactically invalid YAML file raises ConfigurationError."""
    yaml_file = tmp_path / "bad.yaml"
    yaml_file.write_text(": invalid yaml\n")
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        with pytest.raises(ConfigurationError, match="Invalid YAML"):
            load_accounts()


# ---------------------------------------------------------------------------
# load() convenience function
# ---------------------------------------------------------------------------


def test_load_env_only() -> None:
    """load() with all env vars set returns the default account's config."""
    env: dict[str, str] = {
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        cfg = load()
        assert isinstance(cfg, MailConfig)
        assert cfg.imap_host == "imap.env.com"
        assert cfg.username == "env_user"


def test_load_falls_back_to_yaml(tmp_path: Path) -> None:
    """load() delegates to load_accounts and returns default account config."""
    yaml_file = _multi_account_yaml(tmp_path)
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        cfg = load()
        assert isinstance(cfg, MailConfig)
        assert cfg.imap_host == "imap.example.com"


def test_load_missing_config_file() -> None:
    """No env vars AND no config file → ConfigurationError."""
    with mock.patch.dict(
        os.environ, {"MAIL_CONFIG_PATH": "/nonexistent/mail.yaml"}, clear=True
    ):
        with pytest.raises(ConfigurationError):
            load()


def test_load_re_raises_on_invalid_value_not_missing(tmp_path: Path) -> None:
    """load() must NOT fall back to the file when env has an invalid value."""
    yaml_file = _multi_account_yaml(tmp_path)
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(yaml_file),
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
        "MAIL_IMAP_PORT": "not-a-number",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            load()
        msg = str(exc.value)
        assert "MAIL_IMAP_PORT" in msg
        assert "not-a-number" in msg


def test_load_re_raises_on_invalid_tls_not_missing(tmp_path: Path) -> None:
    """load() must re-raise when TLS mode is invalid."""
    yaml_file = _multi_account_yaml(tmp_path)
    env: dict[str, str] = {
        "MAIL_CONFIG_PATH": str(yaml_file),
        "MAIL_IMAP_HOST": "imap.env.com",
        "MAIL_SMTP_HOST": "smtp.env.com",
        "MAIL_USERNAME": "env_user",
        "MAIL_PASSWORD": "env_pass",
        "MAIL_IMAP_TLS_MODE": "tls-9.9",
    }
    with mock.patch.dict(os.environ, env, clear=True):
        with pytest.raises(ConfigurationError) as exc:
            load()
        msg = str(exc.value)
        assert "MAIL_IMAP_TLS_MODE" in msg
        assert "tls-9.9" in msg


# ---------------------------------------------------------------------------
# Edge cases: YAML file that exists but is not a multi-account shape
# ---------------------------------------------------------------------------


def test_load_accounts_yaml_is_list_raises(tmp_path: Path) -> None:
    """A YAML file whose top-level value is a list → not multi-account,
    not a dict with 'accounts' key → falls through to env error."""
    yaml_file = tmp_path / "list.yaml"
    yaml_file.write_text("- item: 1\n")
    with mock.patch.dict(os.environ, _env_with_path(yaml_file), clear=True):
        with pytest.raises(ConfigurationError):
            load_accounts()
